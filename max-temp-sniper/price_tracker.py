"""
Max Temp Sniper — Price Tracker.
Tracks how fast Polymarket prices react after a METAR signal fires.
Snapshots price at signal time, then checks at 30s, 1m, 2m, 5m, 10m intervals.
"""
from __future__ import annotations
import json
import logging
import os
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

from models import LockedBand, TriggerResult

logger = logging.getLogger("sniper.price_tracker")

CLOB_BASE = "https://clob.polymarket.com"

# Intervals to check: (seconds_after_signal, column_name)
TRACK_INTERVALS = [
    (30, "price_at_30s"),
    (60, "price_at_1m"),
    (120, "price_at_2m"),
    (300, "price_at_5m"),
    (600, "price_at_10m"),
]


class PriceTracker:
    """Tracks price movement after signals to measure market reaction speed."""

    def __init__(self):
        self._supabase_url = os.getenv("SUPABASE_URL", "")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        self._lock = threading.Lock()
        # {track_id: {signal_time: datetime, token_id: str, price_at_signal: float, intervals_done: set()}}
        self._pending: dict[str, dict] = {}
        logger.info("PriceTracker initialized")

    def start_tracking(self, trigger: TriggerResult):
        """
        Start tracking ALL locked bands from a trigger.
        For each locked band: fetch current midpoint, insert row, add to pending.
        """
        signal_time = trigger.signal_time

        for lb in trigger.locked_bands:
            try:
                # Always fetch midpoint using YES token (NO tokens 404 on negRisk markets)
                fetch_token_id = lb.band.yes_token_id
                if not fetch_token_id:
                    logger.warning(f"No yes_token_id for {lb.band.label} {lb.side}, skip tracking")
                    continue

                # Fetch YES midpoint, invert for NO side
                yes_mid = self._fetch_midpoint(fetch_token_id)
                if yes_mid is None:
                    logger.warning(f"No midpoint for {lb.band.label} {lb.side}, skip tracking")
                    continue

                price_at_signal = round(1.0 - yes_mid, 6) if lb.side == "NO" else yes_mid
                # Store the YES token for subsequent fetches (it always works)
                token_id = fetch_token_id

                track_id = str(uuid.uuid4())

                # Insert row into sniper_price_tracks
                self._insert_track(
                    track_id=track_id,
                    signal_time=signal_time,
                    trigger=trigger,
                    lb=lb,
                    token_id=token_id,
                    price_at_signal=price_at_signal,
                )

                # Add to pending in-memory
                # token_id is always the YES token; side tells us whether to invert
                with self._lock:
                    self._pending[track_id] = {
                        "signal_time": signal_time,
                        "token_id": token_id,
                        "side": lb.side,
                        "price_at_signal": price_at_signal,
                        "intervals_done": set(),
                    }

                logger.info(
                    f"TRACK started: {lb.market.city} {lb.band.label} {lb.side} "
                    f"price={price_at_signal:.4f} track_id={track_id[:8]}"
                )

            except Exception as e:
                logger.error(f"Failed to start tracking {lb.band.label} {lb.side}: {e}")

    def check_pending(self):
        """
        Check all pending tracks. For each one, if enough time has passed
        since signal_time, fetch current midpoint and update the DB column.
        """
        now = datetime.now(timezone.utc)
        completed = []

        with self._lock:
            pending_snapshot = dict(self._pending)

        for track_id, info in pending_snapshot.items():
            elapsed = (now - info["signal_time"]).total_seconds()
            token_id = info["token_id"]
            price_at_signal = info["price_at_signal"]

            for interval_secs, col_name in TRACK_INTERVALS:
                if col_name in info["intervals_done"]:
                    continue
                if elapsed < interval_secs:
                    continue

                # Time to check this interval
                try:
                    # token_id is always YES token; invert for NO side
                    yes_price = self._fetch_midpoint(token_id)
                    if yes_price is None:
                        logger.warning(f"Midpoint fetch failed for track {track_id[:8]} at {col_name}")
                        continue
                    side = info.get("side", "YES")
                    current_price = round(1.0 - yes_price, 6) if side == "NO" else yes_price

                    # Calculate threshold crossings
                    updates = {col_name: current_price}

                    # Check if this is the first interval crossing 95% or 99% of the way to 1.0
                    if price_at_signal < 1.0:
                        target_95 = price_at_signal + 0.95 * (1.0 - price_at_signal)
                        target_99 = price_at_signal + 0.99 * (1.0 - price_at_signal)

                        if current_price >= target_95 and "time_to_95pct" not in info["intervals_done"]:
                            updates["time_to_95pct"] = interval_secs
                            with self._lock:
                                self._pending[track_id]["intervals_done"].add("time_to_95pct")

                        if current_price >= target_99 and "time_to_99pct" not in info["intervals_done"]:
                            updates["time_to_99pct"] = interval_secs
                            with self._lock:
                                self._pending[track_id]["intervals_done"].add("time_to_99pct")

                    self._update_track(track_id, updates)

                    with self._lock:
                        self._pending[track_id]["intervals_done"].add(col_name)

                    logger.info(
                        f"TRACK update: {track_id[:8]} {col_name}={current_price:.4f} "
                        f"(was {price_at_signal:.4f}, elapsed={elapsed:.0f}s)"
                    )

                except Exception as e:
                    logger.error(f"Failed to check track {track_id[:8]} at {col_name}: {e}")

            # Check if all intervals are done
            with self._lock:
                interval_cols = {col for _, col in TRACK_INTERVALS}
                if interval_cols.issubset(self._pending[track_id]["intervals_done"]):
                    completed.append(track_id)

        # Remove completed tracks
        if completed:
            with self._lock:
                for track_id in completed:
                    self._pending.pop(track_id, None)
            logger.info(f"TRACK completed: {len(completed)} tracks finished all intervals")

    def pending_count(self) -> int:
        """Return number of pending tracks."""
        with self._lock:
            return len(self._pending)

    def _fetch_midpoint(self, token_id: str) -> Optional[float]:
        """Fetch midpoint price from CLOB API.

        Note: Always pass the YES token ID. NO tokens return 404 on negRisk markets.
        Callers should invert (1.0 - mid) for NO side pricing.
        """
        try:
            url = f"{CLOB_BASE}/midpoint?token_id={token_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            mid = data.get("mid")
            return float(mid) if mid is not None else None
        except Exception as e:
            logger.warning(f"Midpoint fetch failed for {token_id[:20]}...: {e}")
            return None

    def _insert_track(
        self,
        track_id: str,
        signal_time: datetime,
        trigger: TriggerResult,
        lb: LockedBand,
        token_id: str,
        price_at_signal: float,
    ):
        """Insert a new row into sniper_price_tracks."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping track insert")
            return

        payload = json.dumps({
            "id": track_id,
            "signal_id": None,  # could link to sniper_signals if available
            "city": lb.market.city or None,
            "station": trigger.station,
            "band_label": lb.band.label,
            "side": lb.side,
            "token_id": token_id,
            "temp_observed": trigger.temp_observed,
            "price_at_signal": price_at_signal,
            "signal_time": signal_time.isoformat(),
        }).encode()

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_price_tracks"
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "apikey": self._supabase_key,
                    "Authorization": f"Bearer {self._supabase_key}",
                    "Prefer": "return=minimal",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.debug(f"Track {track_id[:8]} inserted to Supabase ({resp.status})")
        except Exception as e:
            logger.warning(f"Failed to insert track to Supabase: {e}")

    def _update_track(self, track_id: str, updates: dict):
        """Update columns on an existing sniper_price_tracks row."""
        if not self._supabase_url or not self._supabase_key:
            return

        payload = json.dumps(updates).encode()

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_price_tracks?id=eq.{track_id}"
            req = urllib.request.Request(
                url,
                data=payload,
                method="PATCH",
                headers={
                    "Content-Type": "application/json",
                    "apikey": self._supabase_key,
                    "Authorization": f"Bearer {self._supabase_key}",
                    "Prefer": "return=minimal",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.debug(f"Track {track_id[:8]} updated ({resp.status}): {list(updates.keys())}")
        except Exception as e:
            logger.warning(f"Failed to update track {track_id[:8]}: {e}")
