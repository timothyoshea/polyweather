"""
Max Temp Sniper — Notifier.
Sends Telegram alerts on trades and errors. Gracefully skips if no tokens configured.
"""
from __future__ import annotations
import json
import logging
import os
import urllib.request
from typing import Optional

from models import Trade, TriggerResult

logger = logging.getLogger("sniper.notifier")


class Notifier:
    """Sends alerts via Telegram. No-ops if tokens not configured."""

    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        if self.enabled:
            logger.info("Telegram notifier enabled")
        else:
            logger.info("Telegram notifier disabled (no tokens)")

    def send(self, message: str):
        """Send a message via Telegram. Silently fails if not configured."""
        if not self.enabled:
            return

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = json.dumps({
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    def notify_signal(self, trigger: TriggerResult):
        """Send alert for a new signal."""
        bands_text = "\n".join(
            f"  {lb.side} {lb.band.label} ({lb.trade_type})"
            for lb in trigger.locked_bands
        )
        msg = (
            f"<b>SNIPER SIGNAL</b>\n"
            f"Station: {trigger.station}\n"
            f"Temp: {trigger.temp_observed}C (prev: {trigger.previous_temp}C)\n"
            f"Bands locked: {len(trigger.locked_bands)}\n"
            f"{bands_text}"
        )
        self.send(msg)

    def notify_trades(self, trades: list[Trade]):
        """Send alert for executed trades."""
        if not trades:
            return

        lines = []
        for t in trades:
            lines.append(
                f"  {t.side} {t.band_label} @ {t.entry_price:.4f} (${t.size_usdc})"
            )
        msg = (
            f"<b>PAPER TRADES</b> ({len(trades)})\n"
            + "\n".join(lines)
        )
        self.send(msg)

    def notify_error(self, error: str):
        """Send alert for an error."""
        self.send(f"<b>SNIPER ERROR</b>\n{error}")

    def notify_heartbeat(self, positions: int, daily_pnl: float):
        """Send periodic health heartbeat."""
        self.send(
            f"<b>HEARTBEAT</b>\n"
            f"Open positions: {positions}\n"
            f"Daily P&L: ${daily_pnl:.2f}"
        )
