"""
Max Temp Sniper — Order Executor.
Paper mode: fetches current midpoint, records paper trade to Supabase.
Live mode (future): fires pre-signed orders via CLOB.
"""
from __future__ import annotations
import json
import logging
import os
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

from models import LockedBand, Trade, TriggerResult

logger = logging.getLogger("sniper.executor")

CLOB_BASE = "https://clob.polymarket.com"


class OrderExecutor:
    """Executes trades in paper or live mode."""

    def __init__(self, mode: str = "paper"):
        self.mode = mode  # "paper" or "live"
        self._supabase_url = os.getenv("SUPABASE_URL", "")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        logger.info(f"OrderExecutor initialized in {self.mode.upper()} mode")

    def execute_signal(self, trigger: TriggerResult, trade_size: float) -> list[Trade]:
        """
        Process a full trigger result. For each locked band:
        1. Fetch current midpoint from CLOB
        2. Record signal to Supabase
        3. Record paper trade to Supabase
        """
        if not trigger.has_signal:
            return []

        # Step 1: Insert signal into sniper_signals
        signal_id = self._insert_signal(trigger)

        trades = []
        for locked in trigger.locked_bands:
            trade = self._execute_single(locked, signal_id, trade_size)
            if trade:
                trades.append(trade)

        return trades

    def _execute_single(self, locked: LockedBand, signal_id: Optional[str], trade_size: float) -> Optional[Trade]:
        """Execute a single paper trade for a locked band."""
        # Determine which token to get the midpoint for
        if locked.side == "YES":
            token_id = locked.band.yes_token_id
        else:
            token_id = locked.band.no_token_id

        # Fetch midpoint price
        midpoint = self._fetch_midpoint(token_id)
        if midpoint is None:
            logger.warning(f"Could not fetch midpoint for {locked.band.label} {locked.side}, using 0.50")
            midpoint = 0.50

        if self.mode == "paper":
            return self._paper_trade(locked, signal_id, midpoint, trade_size)
        else:
            # Future: live trading via CLOB
            logger.warning("Live mode not yet implemented, falling back to paper")
            return self._paper_trade(locked, signal_id, midpoint, trade_size)

    def _paper_trade(self, locked: LockedBand, signal_id: Optional[str], midpoint: float, trade_size: float) -> Trade:
        """Record a paper trade to Supabase."""
        now = datetime.now(timezone.utc).isoformat()

        trade = Trade(
            id=str(uuid.uuid4()),
            signal_id=signal_id,
            market_id=locked.market.condition_id,
            market_question=locked.market.question,
            band_label=locked.band.label,
            band_temp=locked.band.temp_value,
            side=locked.side,
            trade_type=locked.trade_type,
            temp_observed=locked.temp_observed,
            entry_price=midpoint,
            size_usdc=trade_size,
            status="open",
            profit_usd=None,
            resolved_at=None,
            created_at=now,
        )

        logger.info(
            f"PAPER TRADE: {locked.side} {locked.band.label} @ {midpoint:.4f} "
            f"(${trade_size}) | type={locked.trade_type} | temp={locked.temp_observed}°C"
        )

        # Insert to Supabase
        self._insert_trade(trade)

        return trade

    def _fetch_midpoint(self, token_id: str) -> Optional[float]:
        """Fetch the current midpoint price for a token from CLOB API."""
        if not token_id:
            return None

        try:
            url = f"{CLOB_BASE}/midpoint?token_id={token_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            mid = data.get("mid")
            if mid is not None:
                return float(mid)
            return None
        except Exception as e:
            logger.warning(f"CLOB midpoint fetch failed for {token_id}: {e}")
            return None

    def _insert_signal(self, trigger: TriggerResult) -> Optional[str]:
        """Insert a signal record into sniper_signals table."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping signal insert")
            return None

        signal_id = str(uuid.uuid4())
        payload = json.dumps({
            "id": signal_id,
            "station": trigger.station,
            "metar_raw": trigger.metar_raw,
            "temp_observed": trigger.temp_observed,
            "previous_temp": trigger.previous_temp,
            "signal_time": trigger.signal_time.isoformat(),
            "num_bands_locked": len(trigger.locked_bands),
            "traded": True,
        }).encode()

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_signals"
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
                logger.info(f"Signal {signal_id} inserted to Supabase ({resp.status})")
        except Exception as e:
            logger.warning(f"Failed to insert signal to Supabase: {e}")

        return signal_id

    def _insert_trade(self, trade: Trade):
        """Insert a trade record into sniper_trades table."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping trade insert")
            return

        payload = json.dumps({
            "id": trade.id,
            "signal_id": trade.signal_id,
            "market_id": trade.market_id,
            "market_question": trade.market_question,
            "band_label": trade.band_label,
            "band_temp": trade.band_temp,
            "side": trade.side,
            "trade_type": trade.trade_type,
            "temp_observed": trade.temp_observed,
            "entry_price": trade.entry_price,
            "size_usdc": trade.size_usdc,
            "status": trade.status,
            "profit_usd": trade.profit_usd,
            "resolved_at": trade.resolved_at,
            "created_at": trade.created_at,
        }).encode()

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_trades"
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
                logger.info(f"Trade {trade.id} inserted to Supabase ({resp.status})")
        except Exception as e:
            logger.warning(f"Failed to insert trade to Supabase: {e}")
