"""
Max Temp Sniper — Order Executor.
Paper mode: fetches current midpoint, records paper trade to Supabase.
Live mode (future): fires pre-signed orders via CLOB.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

from models import LockedBand, Trade, TriggerResult

logger = logging.getLogger("sniper.executor")

CLOB_BASE = "https://clob.polymarket.com"

# Maximum entry price — skip trades above this (no profit potential)
MAX_ENTRY_PRICE = float(os.getenv("MAX_ENTRY_PRICE", "0.98"))

# Month name -> number mapping for date extraction
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _extract_market_date(question: str) -> Optional[str]:
    """
    Parse market date from question text.
    E.g. "Highest temperature in Amsterdam on April 12?" -> "2026-04-12"
    """
    m = re.search(r"on\s+(\w+)\s+(\d{1,2})\b", question, re.IGNORECASE)
    if not m:
        return None
    month_name = m.group(1).lower()
    day = int(m.group(2))
    month_num = _MONTH_MAP.get(month_name)
    if month_num is None:
        return None
    return f"2026-{month_num:02d}-{day:02d}"


class OrderExecutor:
    """Executes trades in paper or live mode."""

    def __init__(self, mode: str = "paper"):
        self.mode = mode  # "paper" or "live"
        self._supabase_url = os.getenv("SUPABASE_URL", "")
        self._supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        self._clob_client = None

        if self.mode == "live":
            try:
                from clob_client import SniperClobClient
                self._clob_client = SniperClobClient()
                if self._clob_client.is_ready():
                    logger.info("OrderExecutor initialized in LIVE mode — ClobClient READY")
                else:
                    logger.warning("OrderExecutor initialized in LIVE mode — ClobClient NOT READY (will paper trade)")
            except Exception as e:
                logger.error(f"Failed to init ClobClient: {e} — falling back to paper mode")
                self._clob_client = None
        else:
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
        """Execute a single paper trade for a locked band.

        For paper trades: grabs the full order book, calculates realistic VWAP
        fill price, and sizes the trade based on ALL available liquidity.
        """
        # Emergency kill switch
        if os.getenv("KILL_SWITCH", "").lower() in ("true", "1", "yes"):
            logger.warning("KILL SWITCH ACTIVE — skipping all trades")
            return None

        token_id = locked.band.no_token_id if locked.side == "NO" else locked.band.yes_token_id

        # For midpoint/book fetching, always use YES token (NO tokens 404 on negRisk markets)
        # Then invert price for NO side
        fetch_token_id = locked.band.yes_token_id

        # Fetch the full order book
        book = self._fetch_full_book(token_id)

        # For YES midpoint (used for potential_trades logging and fallback)
        yes_midpoint = self._fetch_midpoint(fetch_token_id)
        no_midpoint = round(1.0 - yes_midpoint, 6) if yes_midpoint is not None else None

        # If no CLOB book, fall back to midpoint for paper trading
        # (most temp markets have AMM liquidity but empty order books)
        if not book or not book["levels"]:
            # Use the appropriate side's midpoint
            midpoint = no_midpoint if locked.side == "NO" else yes_midpoint
            if midpoint is None or midpoint <= 0:
                logger.warning(f"SKIP (no book & no midpoint): {locked.band.label} {locked.side}")
                self._log_potential_trade(
                    locked, signal_id, yes_midpoint, no_midpoint,
                    None, None, 0, 0, "no_book_no_midpoint", False,
                )
                return None
            if midpoint >= MAX_ENTRY_PRICE:
                logger.info(f"SKIP (price too high): {locked.band.label} {locked.side} mid={midpoint:.4f}")
                self._log_potential_trade(
                    locked, signal_id, yes_midpoint, no_midpoint,
                    None, None, 0, 0, "price_too_high", False,
                )
                return None
            # Create a synthetic book from midpoint
            book = {
                "levels": [{"price": round(midpoint, 4), "size": 0, "cost": 0}],
                "vwap_price": midpoint,
                "total_available_usdc": 0,
                "total_shares": 0,
                "best_bid": None,
                "best_ask": midpoint,
                "bid_depth_usdc": None,
                "ask_depth_usdc": 0,
                "source": "midpoint",
            }
            logger.info(f"No CLOB book for {locked.band.label} {locked.side}, using midpoint={midpoint:.4f}")

        levels = book["levels"]
        vwap_price = book["vwap_price"]
        total_available = book["total_available_usdc"]
        total_shares = book["total_shares"]

        if vwap_price is None or vwap_price <= 0:
            logger.warning(f"SKIP (bad price): {locked.band.label} {locked.side}")
            self._log_potential_trade(
                locked, signal_id, yes_midpoint, no_midpoint,
                book.get("best_bid"), book.get("best_ask"),
                len(levels), total_available, "bad_price", False,
            )
            return None

        if vwap_price >= MAX_ENTRY_PRICE:
            logger.info(
                f"SKIP (price too high): {locked.band.label} {locked.side} "
                f"VWAP={vwap_price:.4f} >= {MAX_ENTRY_PRICE} — no profit potential"
            )
            self._log_potential_trade(
                locked, signal_id, yes_midpoint, no_midpoint,
                book.get("best_bid"), book.get("best_ask"),
                len(levels), total_available, "price_too_high", False,
            )
            return None

        # Log as traded
        self._log_potential_trade(
            locked, signal_id, yes_midpoint, no_midpoint,
            book.get("best_bid"), book.get("best_ask"),
            len(levels), total_available, None, True,
        )

        if self.mode == "live" and self._clob_client and self._clob_client.is_ready():
            return self._live_trade(locked, signal_id, book, token_id)
        else:
            if self.mode == "live":
                logger.warning("Live mode requested but ClobClient not ready, falling back to paper")
            return self._paper_trade(locked, signal_id, book, token_id)

    def _paper_trade(self, locked: LockedBand, signal_id: Optional[str], book: dict, token_id: str) -> Trade:
        """Record a paper trade using the full order book snapshot.

        Uses VWAP fill price and total available liquidity — not a fixed size.
        This shows what we'd actually get if we swept the entire book.
        """
        now = datetime.now(timezone.utc).isoformat()

        vwap_price = book["vwap_price"]
        total_cost = book["total_available_usdc"]
        total_shares = book["total_shares"]
        levels = book["levels"]
        num_levels = len(levels)

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
            entry_price=vwap_price,
            size_usdc=round(total_cost, 4),
            status="open",
            profit_usd=None,
            resolved_at=None,
            created_at=now,
        )

        logger.info(
            f"PAPER TRADE: {locked.side} {locked.band.label} VWAP={vwap_price:.4f} "
            f"${total_cost:.2f} ({total_shares:.1f} shares, {num_levels} levels) | "
            f"type={locked.trade_type} | temp={locked.temp_observed}°C"
        )

        city = locked.market.city or None
        market_date = _extract_market_date(locked.market.question)

        self._insert_trade(
            trade, token_id=token_id, city=city, market_date=market_date,
            book_depth={
                "best_bid": book.get("best_bid"),
                "best_ask": book.get("best_ask"),
                "bid_depth_usdc": book.get("bid_depth_usdc"),
                "ask_depth_usdc": book.get("ask_depth_usdc"),
            },
            book_levels=levels,
            fill_price=vwap_price,
            available_liquidity=total_cost,
            num_levels=num_levels,
            total_shares=total_shares,
        )

        return trade

    def _live_trade(self, locked: LockedBand, signal_id: Optional[str], book: dict, token_id: str) -> Optional[Trade]:
        """Execute a live trade via CLOB API.

        Uses the best ask from the order book as the limit price.
        Polls fill status for up to 10 seconds after placement.
        """
        now = datetime.now(timezone.utc).isoformat()
        levels = book["levels"]
        best_ask = book.get("best_ask")
        vwap_price = book["vwap_price"]
        total_cost = book["total_available_usdc"]
        total_shares = book["total_shares"]

        # Use best ask as limit price (most likely to fill immediately)
        limit_price = best_ask if best_ask and best_ask > 0 else vwap_price
        if limit_price is None or limit_price <= 0:
            logger.warning(f"LIVE SKIP (no valid price): {locked.band.label} {locked.side}")
            return None

        # Check balance before placing order
        bal = self._clob_client.get_balance()
        if bal is None:
            logger.warning(f"LIVE SKIP (balance check failed): {locked.band.label}")
            return None

        usdc_available = bal["balance_usdc"]
        estimated_cost = round(limit_price * total_shares, 4) if total_shares > 0 else 0

        if estimated_cost > 0 and usdc_available < estimated_cost:
            logger.warning(
                f"LIVE SKIP (insufficient balance): need ${estimated_cost:.2f} "
                f"but only ${usdc_available:.2f} available for {locked.band.label}"
            )
            return None

        # Determine size: use all available shares from the book
        size = round(total_shares, 2) if total_shares > 0 else 0
        if size <= 0:
            logger.warning(f"LIVE SKIP (zero size): {locked.band.label}")
            return None

        logger.info(
            f"LIVE ORDER: {locked.side} {locked.band.label} "
            f"price={limit_price:.4f} size={size:.2f} shares "
            f"est_cost=${estimated_cost:.2f} balance=${usdc_available:.2f}"
        )

        # Place the order
        order_resp = self._clob_client.place_order(
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=size,
        )

        if order_resp is None:
            logger.error(f"LIVE TRADE FAILED: order placement returned None for {locked.band.label}")
            return None

        order_id = order_resp.get("order_id", "")
        order_status = order_resp.get("status", "")
        logger.info(f"  Order placed: id={order_id} status={order_status}")

        # Poll fill status for up to 10 seconds
        fill_status = order_status
        size_matched = "0"
        for _ in range(5):
            time.sleep(2)
            if not order_id:
                break
            order_info = self._clob_client.get_order(order_id)
            if order_info:
                fill_status = order_info.get("status", fill_status)
                size_matched = order_info.get("size_matched", "0")
                logger.info(f"  Poll: status={fill_status} matched={size_matched}")
                if fill_status in ("MATCHED", "FILLED"):
                    break

        # Check balance after to calculate actual cost
        actual_cost = None
        bal_after = self._clob_client.get_balance()
        if bal_after and bal is not None:
            actual_cost = round(usdc_available - bal_after["balance_usdc"], 6)

        # Build trade record
        fill_price = limit_price  # Best approximation
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
            entry_price=fill_price,
            size_usdc=actual_cost if actual_cost and actual_cost > 0 else estimated_cost,
            status="open",
            profit_usd=None,
            resolved_at=None,
            created_at=now,
        )

        logger.info(
            f"LIVE TRADE: {locked.side} {locked.band.label} "
            f"price={fill_price:.4f} est=${estimated_cost:.2f} actual=${actual_cost} "
            f"fill_status={fill_status} matched={size_matched} "
            f"order_id={order_id}"
        )

        city = locked.market.city or None
        market_date = _extract_market_date(locked.market.question)

        self._insert_trade(
            trade, token_id=token_id, city=city, market_date=market_date,
            book_depth={
                "best_bid": book.get("best_bid"),
                "best_ask": book.get("best_ask"),
                "bid_depth_usdc": book.get("bid_depth_usdc"),
                "ask_depth_usdc": book.get("ask_depth_usdc"),
            },
            book_levels=levels,
            fill_price=fill_price,
            available_liquidity=total_cost,
            num_levels=len(levels),
            total_shares=total_shares,
            trade_mode="live",
            order_id=order_id,
            fill_status=fill_status,
            actual_cost_usdc=actual_cost,
        )

        return trade

    def _fetch_midpoint(self, token_id: str) -> Optional[float]:
        """Fetch midpoint price from CLOB API.

        Note: For negRisk temperature markets, NO token IDs return 404.
        Callers should pass the YES token ID and invert for NO side.
        """
        if not token_id:
            return None
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

    def _fetch_full_book(self, token_id: str) -> Optional[dict]:
        """Fetch full order book from CLOB API.

        Returns dict with:
          levels: list of {price, size, side} dicts (the asks we'd buy into, sorted best first)
          vwap_price: volume-weighted average price across all levels
          total_available_usdc: total cost to sweep the entire book
          total_shares: total shares available
          best_bid, best_ask, bid_depth_usdc, ask_depth_usdc
        """
        if not token_id:
            return None

        try:
            url = f"{CLOB_BASE}/book?token_id={token_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "MaxTempSniper/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not asks and not bids:
                return None

            # Parse and sort asks (what we'd buy into) — lowest price first
            parsed_asks = sorted(
                [{"price": float(a["price"]), "size": float(a["size"])} for a in asks],
                key=lambda x: x["price"]
            )

            # Calculate VWAP and totals across ALL ask levels
            total_cost = 0.0
            total_shares = 0.0
            levels = []
            for a in parsed_asks:
                cost = a["price"] * a["size"]
                total_cost += cost
                total_shares += a["size"]
                levels.append({
                    "price": round(a["price"], 4),
                    "size": round(a["size"], 2),
                    "cost": round(cost, 4),
                })

            vwap_price = round(total_cost / total_shares, 6) if total_shares > 0 else None

            # Bid side summary
            best_bid = max((float(b["price"]) for b in bids), default=None)
            best_ask = min((float(a["price"]) for a in asks), default=None)
            bid_depth_usdc = round(sum(float(b["price"]) * float(b["size"]) for b in bids), 4) if bids else None
            ask_depth_usdc = round(total_cost, 4)

            return {
                "levels": levels,
                "vwap_price": vwap_price,
                "total_available_usdc": round(total_cost, 4),
                "total_shares": round(total_shares, 4),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_depth_usdc": bid_depth_usdc,
                "ask_depth_usdc": ask_depth_usdc,
            }
        except Exception as e:
            logger.warning(f"CLOB book fetch failed for {token_id}: {e}")
            return None

    def _log_potential_trade(
        self,
        locked: LockedBand,
        signal_id: Optional[str],
        midpoint_yes: Optional[float],
        midpoint_no: Optional[float],
        best_bid: Optional[float],
        best_ask: Optional[float],
        book_levels_count: int,
        available_liquidity_usdc: float,
        skip_reason: Optional[str],
        was_traded: bool,
    ):
        """Log every band evaluation to sniper_potential_trades (traded or skipped)."""
        if not self._supabase_url or not self._supabase_key:
            return

        payload = json.dumps({
            "signal_id": signal_id,
            "city": locked.market.city or None,
            "station": locked.market.station or None,
            "band_label": locked.band.label,
            "side": locked.side,
            "trade_type": locked.trade_type,
            "temp_observed": locked.temp_observed,
            "yes_token_id": locked.band.yes_token_id,
            "no_token_id": locked.band.no_token_id,
            "midpoint_yes": midpoint_yes,
            "midpoint_no": midpoint_no,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "book_levels_count": book_levels_count,
            "available_liquidity_usdc": round(available_liquidity_usdc, 4) if available_liquidity_usdc else 0,
            "skip_reason": skip_reason,
            "was_traded": was_traded,
        }).encode()

        try:
            url = f"{self._supabase_url}/rest/v1/sniper_potential_trades"
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
                logger.debug(
                    f"Potential trade logged: {locked.band.label} {locked.side} "
                    f"traded={was_traded} skip={skip_reason} ({resp.status})"
                )
        except Exception as e:
            logger.warning(f"Failed to log potential trade: {e}")

    def _insert_signal(self, trigger: TriggerResult) -> Optional[str]:
        """Insert a signal record into sniper_signals table."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping signal insert")
            return None

        signal_id = str(uuid.uuid4())

        # Extract city and market_date from the first locked band
        city = None
        market_date = None
        if trigger.locked_bands:
            city = trigger.locked_bands[0].market.city or None
            market_date = _extract_market_date(trigger.locked_bands[0].market.question)

        payload = json.dumps({
            "id": signal_id,
            "station": trigger.station,
            "metar_raw": trigger.metar_raw,
            "temp_observed": trigger.temp_observed,
            "previous_temp": trigger.previous_temp,
            "signal_time": trigger.signal_time.isoformat(),
            "num_bands_locked": len(trigger.locked_bands),
            "traded": True,
            "city": city,
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

    def _insert_trade(
        self,
        trade: Trade,
        token_id: Optional[str] = None,
        city: Optional[str] = None,
        market_date: Optional[str] = None,
        book_depth: Optional[dict] = None,
        book_levels: Optional[list] = None,
        fill_price: Optional[float] = None,
        available_liquidity: Optional[float] = None,
        num_levels: Optional[int] = None,
        total_shares: Optional[float] = None,
        trade_mode: Optional[str] = None,
        order_id: Optional[str] = None,
        fill_status: Optional[str] = None,
        actual_cost_usdc: Optional[float] = None,
    ):
        """Insert a trade record into sniper_trades table."""
        if not self._supabase_url or not self._supabase_key:
            logger.debug("No Supabase config, skipping trade insert")
            return

        # Use provided total_shares or calculate from size/price
        if total_shares is None:
            total_shares = round(trade.size_usdc / trade.entry_price, 4) if trade.entry_price > 0 else 0

        bd = book_depth or {}
        payload = json.dumps({
            "signal_id": trade.signal_id,
            "market_id": trade.market_id,
            "market_question": trade.market_question,
            "band_label": trade.band_label,
            "band_temp": int(trade.band_temp),
            "side": trade.side,
            "trade_type": trade.trade_type,
            "temp_observed": trade.temp_observed,
            "entry_price": trade.entry_price,
            "size_usdc": trade.size_usdc,
            "total_shares": round(total_shares, 4),
            "expected_profit": round(total_shares - trade.size_usdc, 4) if total_shares > 0 else 0,
            "status": trade.status,
            "token_id": token_id,
            "city": city,
            "market_date": market_date,
            "best_bid": bd.get("best_bid"),
            "best_ask": bd.get("best_ask"),
            "bid_depth_usdc": bd.get("bid_depth_usdc"),
            "ask_depth_usdc": bd.get("ask_depth_usdc"),
            "book_levels": book_levels,
            "fill_price": fill_price,
            "available_liquidity_usdc": available_liquidity,
            "num_levels": num_levels,
            "trade_mode": trade_mode or self.mode,
            "order_id": order_id,
            "fill_status": fill_status,
            "actual_cost_usdc": actual_cost_usdc,
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
