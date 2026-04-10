"""Max Temp Sniper — CLOB Client wrapper for live trading."""
from __future__ import annotations
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("sniper.clob")

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon


class SniperClobClient:
    """Thin wrapper around py-clob-client for the Max Temp Sniper."""

    def __init__(self):
        self._client = None
        self._ready = False
        self._init_client()

    def _init_client(self):
        """Initialize the ClobClient from env vars."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            from py_clob_client.constants import POLYGON

            private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
            if not private_key:
                logger.error("POLYMARKET_PRIVATE_KEY not set — live trading disabled")
                return

            api_key = os.environ.get("CLOB_API_KEY", "")
            api_secret = os.environ.get("CLOB_API_SECRET", "")
            api_passphrase = os.environ.get("CLOB_API_PASSPHRASE", "")

            if api_key and api_secret and api_passphrase:
                # Fast path: reuse saved creds
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                )
                self._client = ClobClient(HOST, key=private_key, chain_id=POLYGON, creds=creds)
                logger.info("ClobClient initialized with saved API creds")
            else:
                # Slow path: derive creds from private key
                self._client = ClobClient(HOST, key=private_key, chain_id=POLYGON, signature_type=0)
                creds = self._client.create_or_derive_api_creds()
                self._client.set_api_creds(creds)
                logger.info("ClobClient initialized — derived API creds")
                logger.info("  CLOB credentials derived successfully")

            self._ready = True

        except Exception as e:
            logger.error(f"ClobClient init failed: {e}", exc_info=True)
            self._client = None
            self._ready = False

    def is_ready(self) -> bool:
        """True if client initialized with valid creds."""
        return self._ready and self._client is not None

    def get_balance(self) -> Optional[dict]:
        """Return USDC balance from CLOB.

        Returns dict with 'balance_usdc' (float) and 'allowance', or None on error.
        """
        if not self.is_ready():
            logger.warning("get_balance called but client not ready")
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

            result = self._client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            balance_raw = result.get("balance", "0")
            # CLOB returns balance in micro-USDC (6 decimals)
            balance_usdc = float(balance_raw) / 1e6
            return {
                "balance_usdc": balance_usdc,
                "allowance": result.get("allowance", "0"),
                "raw": balance_raw,
            }
        except Exception as e:
            logger.error(f"get_balance failed: {e}")
            return None

    def place_order(self, token_id: str, side: str, price: float, size: float) -> Optional[dict]:
        """Place a GTC limit order.

        Args:
            token_id: The token to buy (YES token ID or NO token ID).
            side: "BUY" (we always buy — the token_id determines YES vs NO).
            price: Limit price (0 < price < 1).
            size: Number of shares to buy.

        Returns dict with order_id, status, result — or None on error.
        """
        if not self.is_ready():
            logger.error("place_order called but client not ready")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY as CLOB_BUY

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=round(size, 2),
                side=CLOB_BUY,
            )

            signed_order = self._client.create_order(order_args)
            result = self._client.post_order(signed_order, orderType=OrderType.GTC)

            order_id = result.get("orderID", result.get("order_id", ""))
            status = result.get("status", "")

            logger.info(
                f"Order placed: {side} {size:.2f} shares @ {price:.4f} "
                f"token={token_id[:20]}... order_id={order_id} status={status}"
            )

            return {
                "order_id": order_id,
                "status": status,
                "result": result,
            }

        except Exception as e:
            logger.error(f"place_order failed: {e}", exc_info=True)
            return None

    def get_order(self, order_id: str) -> Optional[dict]:
        """Poll order fill status.

        Returns order info dict with status, size_matched, etc. — or None on error.
        """
        if not self.is_ready():
            logger.warning("get_order called but client not ready")
            return None

        try:
            order_info = self._client.get_order(order_id)
            return {
                "order_id": order_id,
                "status": order_info.get("status", ""),
                "original_size": order_info.get("original_size"),
                "size_matched": order_info.get("size_matched", "0"),
                "price": order_info.get("price"),
                "side": order_info.get("side"),
                "associate_trades": order_info.get("associate_trades", []),
            }
        except Exception as e:
            logger.error(f"get_order failed for {order_id}: {e}")
            return None
