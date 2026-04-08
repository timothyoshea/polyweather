"""
Max Temp Sniper — Risk Manager.
Daily loss limit, max per-trade size, position count limits.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from models import LockedBand

logger = logging.getLogger("sniper.risk")


class RiskManager:
    """Enforces risk limits on paper and live trades."""

    def __init__(self):
        self.max_trade_size_usdc = float(os.getenv("MAX_TRADE_SIZE_USDC", "10.0"))
        self.daily_loss_limit_usdc = float(os.getenv("DAILY_LOSS_LIMIT_USDC", "50.0"))
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", "20"))
        self.default_trade_size_usdc = float(os.getenv("DEFAULT_TRADE_SIZE_USDC", "5.0"))

        # Track daily P&L
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._current_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_trade_allowed(self, locked: LockedBand, current_positions: int) -> tuple[bool, str]:
        """
        Check if a trade is allowed under risk limits.
        Returns (allowed, reason).
        """
        # Reset daily counters on new day
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._current_date = today
            logger.info("Daily risk counters reset")

        # Check daily loss limit
        if self._daily_pnl <= -self.daily_loss_limit_usdc:
            reason = f"Daily loss limit hit: ${self._daily_pnl:.2f} <= -${self.daily_loss_limit_usdc:.2f}"
            logger.warning(reason)
            return False, reason

        # Check max open positions
        if current_positions >= self.max_open_positions:
            reason = f"Max positions reached: {current_positions} >= {self.max_open_positions}"
            logger.warning(reason)
            return False, reason

        return True, "OK"

    def get_trade_size(self, entry_price: float) -> float:
        """Calculate trade size in USDC, capped at max."""
        size = min(self.default_trade_size_usdc, self.max_trade_size_usdc)
        return round(size, 2)

    def record_trade_result(self, pnl: float):
        """Record a trade's P&L for daily tracking."""
        self._daily_pnl += pnl
        self._daily_trades += 1
        logger.info(f"Daily P&L: ${self._daily_pnl:.2f} ({self._daily_trades} trades)")

    def status(self) -> dict:
        return {
            "daily_pnl": self._daily_pnl,
            "daily_trades": self._daily_trades,
            "max_trade_size": self.max_trade_size_usdc,
            "daily_loss_limit": self.daily_loss_limit_usdc,
            "max_positions": self.max_open_positions,
        }
