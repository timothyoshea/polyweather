"""
Max Temp Sniper — Main entrypoint.
Asyncio loops: market refresh (15min), METAR poll (10s), health heartbeat (5min).
Polls ALL active temperature markets across all cities.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, date

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

from market_scanner import fetch_all_markets
from metar_poller import MetarPoller
from signal_engine import SignalEngine
from order_executor import OrderExecutor
from position_tracker import PositionTracker
from risk_manager import RiskManager
from notifier import Notifier

# Intervals (seconds)
MARKET_REFRESH_INTERVAL = int(os.getenv("MARKET_REFRESH_INTERVAL", "900"))   # 15 min
METAR_POLL_INTERVAL = int(os.getenv("METAR_POLL_INTERVAL", "10"))            # 10 sec
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "300"))             # 5 min

# Components
poller = MetarPoller()
executor = OrderExecutor(mode=os.getenv("TRADE_MODE", "paper"))
tracker = PositionTracker()
risk = RiskManager()
notifier = Notifier()
signal_engine = SignalEngine(markets=[])


async def market_refresh_loop():
    """Refresh market data from Polymarket every 15 minutes."""
    while True:
        try:
            logger.info("Refreshing ALL temperature markets...")
            markets = await asyncio.get_event_loop().run_in_executor(
                None, fetch_all_markets
            )
            signal_engine.update_markets(markets)

            # Build station metadata mapping for METAR poller
            station_meta = {}
            for m in markets:
                if m.station and m.station not in station_meta:
                    station_meta[m.station] = {
                        "city": m.city,
                        "resolution_source": m.resolution_source,
                    }
            poller.set_station_metadata(station_meta)

            band_count = sum(len(m.bands) for m in markets)
            stations = set(m.station for m in markets)
            cities = set(m.city for m in markets)
            logger.info(
                f"Markets refreshed: {len(markets)} markets across "
                f"{len(cities)} cities / {len(stations)} stations, "
                f"{band_count} total bands"
            )
        except Exception as e:
            logger.error(f"Market refresh error: {e}", exc_info=True)
            notifier.notify_error(f"Market refresh failed: {e}")

        await asyncio.sleep(MARKET_REFRESH_INTERVAL)


async def metar_poll_loop():
    """Poll METAR data every 10 seconds for all active stations."""
    # Wait for initial market load
    await asyncio.sleep(5)

    while True:
        try:
            # Get unique stations from active markets
            stations = list(set(m.station for m in signal_engine.markets if m.station))

            if not stations:
                logger.debug("No stations to poll")
                await asyncio.sleep(METAR_POLL_INTERVAL)
                continue

            # Batch poll all stations
            triggers_raw = await asyncio.get_event_loop().run_in_executor(
                None, poller.poll_all, stations
            )

            # Process each rising trigger
            for metar in triggers_raw:
                station = metar["station"]
                temp = metar["temp"]
                prev = metar["previous_temp"]

                # Find markets for this station
                station_markets = [m for m in signal_engine.markets if m.station == station]

                for market in station_markets:
                    trigger = signal_engine.evaluate_market(metar, market)

                    if not trigger.has_signal:
                        continue

                    logger.info(
                        f"TRIGGER {market.city} ({station}): {temp}°C (was {prev}°C), "
                        f"{len(trigger.locked_bands)} bands locked"
                    )
                    notifier.notify_signal(trigger)

                    # Filter already-traded and risk-blocked bands
                    new_bands = []
                    for lb in trigger.locked_bands:
                        if tracker.has_position(lb):
                            logger.info(f"  SKIP (existing): {lb.band.label} {lb.side}")
                        else:
                            allowed, reason = risk.check_trade_allowed(lb, tracker.position_count())
                            if allowed:
                                new_bands.append(lb)
                            else:
                                logger.info(f"  SKIP (risk): {lb.band.label} {lb.side} - {reason}")

                    if not new_bands:
                        logger.info(f"  All bands already traded or risk-blocked")
                        continue

                    trigger.locked_bands = new_bands
                    trade_size = risk.get_trade_size(0.50)

                    trades = await asyncio.get_event_loop().run_in_executor(
                        None, executor.execute_signal, trigger, trade_size
                    )

                    for lb, trade in zip(new_bands, trades):
                        tracker.record_position(lb, trade.entry_price, trade.size_usdc)

                    notifier.notify_trades(trades)
                    logger.info(f"  Executed {len(trades)} paper trades for {market.city}")

        except Exception as e:
            logger.error(f"METAR poll error: {e}", exc_info=True)
            notifier.notify_error(f"METAR poll failed: {e}")

        await asyncio.sleep(METAR_POLL_INTERVAL)


async def heartbeat_loop():
    """Periodic health heartbeat logging."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            positions = tracker.position_count()
            risk_status = risk.status()
            stations = set(m.station for m in signal_engine.markets)
            logger.info(
                f"HEARTBEAT: {len(signal_engine.markets)} markets, "
                f"{len(stations)} stations, {positions} positions, "
                f"daily P&L: ${risk_status['daily_pnl']:.2f}"
            )
            notifier.notify_heartbeat(positions, risk_status["daily_pnl"])
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")


async def main():
    """Start all loops."""
    logger.info("=" * 60)
    logger.info("MAX TEMP SNIPER starting (ALL CITIES)")
    logger.info(f"  Mode: {executor.mode.upper()}")
    logger.info(f"  Market refresh: {MARKET_REFRESH_INTERVAL}s")
    logger.info(f"  METAR poll: {METAR_POLL_INTERVAL}s")
    logger.info(f"  Heartbeat: {HEARTBEAT_INTERVAL}s")
    logger.info(f"  Max trade size: ${risk.max_trade_size_usdc}")
    logger.info(f"  Daily loss limit: ${risk.daily_loss_limit_usdc}")
    logger.info(f"  Max positions: {risk.max_open_positions}")
    logger.info("=" * 60)

    tracker.load_from_supabase()

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
