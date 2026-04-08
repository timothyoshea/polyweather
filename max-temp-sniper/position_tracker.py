"""
Max Temp Sniper — Position Tracker.
Tracks (market_id, band_label, side) to prevent double entry.
Persists open positions to Supabase.
"""
from __future__ import annotations
import json
import logging
import os
import urllib.request
from typing import Optional

from models import Position, LockedBand

logger = logging.getLogger("sniper.positions")


class PositionTracker:
    """Prevents double entry on the same band/side combo. Syncs with Supabase."""

    def __init__(self):
        # Key: (market_id, band_label, side)
        self._positions: dict[tuple[str, str, str], Position] = {}
        self._supabase_url = os.getenv("SUPABASE_URL", "")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")

    def _pos_key(self, market_id: str, band_label: str, side: str) -> tuple[str, str, str]:
        return (market_id, band_label, side)

    def has_position(self, locked: LockedBand) -> bool:
        """Check if we already have a position for this band/side."""
        key = self._pos_key(
            locked.market.condition_id,
            locked.band.label,
            locked.side,
        )
        return key in self._positions

    def record_position(self, locked: LockedBand, entry_price: float, size_usdc: float):
        """Record a new position locally and persist to Supabase."""
        from datetime import datetime, timezone
        key = self._pos_key(
            locked.market.condition_id,
            locked.band.label,
            locked.side,
        )
        pos = Position(
            market_id=locked.market.condition_id,
            band_label=locked.band.label,
            side=locked.side,
            trade_type=locked.trade_type,
            entry_price=entry_price,
            size_usdc=size_usdc,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._positions[key] = pos
        logger.info(f"Position recorded: {locked.band.label} {locked.side} @ {entry_price}")

        # Persist to Supabase
        self._persist_position(pos)

    def get_open_positions(self) -> list[Position]:
        """Return all tracked positions."""
        return list(self._positions.values())

    def position_count(self) -> int:
        return len(self._positions)

    def _persist_position(self, pos: Position):
        """Upsert position to Supabase sniper_positions table (best effort)."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping position persist")
            return

        try:
            payload = json.dumps({
                "market_id": pos.market_id,
                "band_label": pos.band_label,
                "side": pos.side,
                "trade_type": pos.trade_type,
                "entry_price": pos.entry_price,
                "size_usdc": pos.size_usdc,
                "created_at": pos.created_at,
            }).encode()

            url = f"{self._supabase_url}/rest/v1/sniper_positions"
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
                logger.debug(f"Position persisted to Supabase: {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to persist position to Supabase: {e}")

    def load_from_supabase(self):
        """Load existing open positions from Supabase on startup."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping position load")
            return

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_trades?status=eq.open&select=market_id,band_label,side,trade_type,entry_price,size_usdc,created_at"
            req = urllib.request.Request(
                url,
                headers={
                    "apikey": self._supabase_key,
                    "Authorization": f"Bearer {self._supabase_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                rows = json.loads(resp.read().decode())

            for row in rows:
                key = self._pos_key(row["market_id"], row["band_label"], row["side"])
                self._positions[key] = Position(
                    market_id=row["market_id"],
                    band_label=row["band_label"],
                    side=row["side"],
                    trade_type=row.get("trade_type", ""),
                    entry_price=row.get("entry_price", 0),
                    size_usdc=row.get("size_usdc", 0),
                    created_at=row.get("created_at", ""),
                )

            logger.info(f"Loaded {len(rows)} open positions from Supabase")
        except Exception as e:
            logger.warning(f"Failed to load positions from Supabase: {e}")
