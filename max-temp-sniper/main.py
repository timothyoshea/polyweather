"""
Max Temp Sniper — Main entrypoint.
Asyncio loops: market refresh (15min), METAR poll (60s), health heartbeat (5min).
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("sniper.main")

from market_scanner import fetch_london_markets
from metar_poller import MetarPoller
from signal_engine import SignalEngine
from order_executor import OrderExecutor
from position_tracker import PositionTracker
from risk_manager import RiskManager
from notifier import Notifier

# Intervals (seconds)
MARKET_REFRESH_INTERVAL = int(os.getenv("MARKET_REFRESH_INTERVAL", "900"))   # 15 min
METAR_POLL_INTERVAL = int(os.getenv("METAR_POLL_INTERVAL", "60"))            # 60 sec
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "300"))             # 5 min

# Components (initialized after env is loaded)
poller = MetarPoller(station=os.getenv("METAR_STATION", "EGLC"))
executor = OrderExecutor(mode=os.getenv("TRADE_MODE", "paper"))
tracker = PositionTracker()
risk = RiskManager()
notifier = Notifier()

# Signal engine gets markets injected after first scan
signal_engine = SignalEngine(markets=[])


async def market_refresh_loop():
    """Refresh market data from Polymarket every 15 minutes."""
    while True:
        try:
            logger.info("Refreshing London temperature markets...")
            markets = await asyncio.get_event_loop().run_in_executor(
                None, fetch_london_markets
            )
            signal_engine.update_markets(markets)

            band_count = sum(len(m.bands) for m in markets)
            logger.info(
                f"Markets refreshed: {len(markets)} markets, {band_count} total bands"
            )
        except Exception as e:
            logger.error(f"Market refresh error: {e}", exc_info=True)
            notifier.notify_error(f"Market refresh failed: {e}")

        await asyncio.sleep(MARKET_REFRESH_INTERVAL)


async def metar_poll_loop():
    """Poll METAR data every 60 seconds and fire signals."""
    # Wait for initial market load
    await asyncio.sleep(5)

    while True:
        try:
            metar = await asyncio.get_event_loop().run_in_executor(None, poller.poll)

            if metar is None:
                logger.debug("No METAR data")
            elif metar.get("is_new") and metar.get("is_rising"):
                # Evaluate signal
                trigger = signal_engine.evaluate(metar)

                if trigger.has_signal:
                    logger.info(
                        f"TRIGGER: {trigger.temp_observed}C, "
                        f"{len(trigger.locked_bands)} bands locked"
                    )

                    # Notify
                    notifier.notify_signal(trigger)

                    # Filter out bands we already have positions on
                    new_bands = []
                    for lb in trigger.locked_bands:
                        if tracker.has_position(lb):
                            logger.info(f"  SKIP (existing position): {lb.band.label} {lb.side}")
                        else:
                            # Risk check
                            allowed, reason = risk.check_trade_allowed(
                                lb, tracker.position_count()
                            )
                            if allowed:
                                new_bands.append(lb)
                            else:
                                logger.info(f"  SKIP (risk): {lb.band.label} {lb.side} - {reason}")

                    if new_bands:
                        # Update trigger with filtered bands
                        trigger.locked_bands = new_bands

                        # Get trade size
                        trade_size = risk.get_trade_size(0.50)

                        # Execute
                        trades = await asyncio.get_event_loop().run_in_executor(
                            None, executor.execute_signal, trigger, trade_size
                        )

                        # Track positions
                        for lb, trade in zip(new_bands, trades):
                            tracker.record_position(lb, trade.entry_price, trade.size_usdc)

                        # Notify trades
                        notifier.notify_trades(trades)

                        logger.info(f"Executed {len(trades)} paper trades")
                    else:
                        logger.info("All locked bands already have positions or blocked by risk")
                else:
                    logger.debug(f"METAR {metar['temp']}C: no signal (rising but no bands locked)")
            else:
                temp = metar.get("temp", "?")
                new = metar.get("is_new", False)
                rising = metar.get("is_rising", False)
                logger.debug(f"METAR {temp}C: new={new}, rising={rising}")

        except Exception as e:
            logger.error(f"METAR poll error: {e}", exc_info=True)
            notifier.notify_error(f"METAR poll failed: {e}")

        await asyncio.sleep(METAR_POLL_INTERVAL)


async def heartbeat_loop():
    """Periodic health heartbeat logging and notification."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            positions = tracker.position_count()
            risk_status = risk.status()
            logger.info(
                f"HEARTBEAT: {positions} positions, "
                f"daily P&L: ${risk_status['daily_pnl']:.2f}, "
                f"daily trades: {risk_status['daily_trades']}"
            )
            notifier.notify_heartbeat(positions, risk_status["daily_pnl"])
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")


async def main():
    """Start all loops."""
    logger.info("=" * 60)
    logger.info("MAX TEMP SNIPER starting")
    logger.info(f"  Mode: {executor.mode.upper()}")
    logger.info(f"  Station: {poller.station}")
    logger.info(f"  Market refresh: {MARKET_REFRESH_INTERVAL}s")
    logger.info(f"  METAR poll: {METAR_POLL_INTERVAL}s")
    logger.info(f"  Heartbeat: {HEARTBEAT_INTERVAL}s")
    logger.info(f"  Max trade size: ${risk.max_trade_size_usdc}")
    logger.info(f"  Daily loss limit: ${risk.daily_loss_limit_usdc}")
    logger.info(f"  Max positions: {risk.max_open_positions}")
    logger.info("=" * 60)

    # Load existing positions from Supabase
    tracker.load_from_supabase()

    # Start all loops concurrently
    await asyncio.gather(
        market_refresh_loop(),
        metar_poll_loop(),
        heartbeat_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
