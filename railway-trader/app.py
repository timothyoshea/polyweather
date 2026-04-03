"""
PolyWeather Railway Trader Service.

Thin execution layer that holds the private key and places orders
on Polymarket via the CLOB API. Called by the Vercel scanner.

Endpoints:
  POST /execute  — place a limit order (GTC)
  GET  /balance  — return USDC balance
  GET  /health   — healthcheck
  GET  /orders   — list open orders
  POST /cancel   — cancel an order or all orders
"""
import os
import json
import traceback
from functools import wraps
from flask import Flask, request, jsonify
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, OrderType,
    BalanceAllowanceParams, AssetType, OpenOrderParams,
)
from py_clob_client.constants import POLYGON
from py_clob_client.order_builder.constants import BUY, SELL

app = Flask(__name__)

# --- Config ---
PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

# Optional: saved CLOB API creds for faster startup
CLOB_API_KEY = os.environ.get("CLOB_API_KEY", "")
CLOB_API_SECRET = os.environ.get("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.environ.get("CLOB_API_PASSPHRASE", "")

# Singleton client
_client = None


def get_client():
    """Get or create the authenticated CLOB client."""
    global _client
    if _client is not None:
        return _client

    if not PRIVATE_KEY:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY not set")

    host = "https://clob.polymarket.com"

    if CLOB_API_KEY and CLOB_API_SECRET and CLOB_API_PASSPHRASE:
        # Fast path: reuse saved creds
        creds = ApiCreds(
            api_key=CLOB_API_KEY,
            api_secret=CLOB_API_SECRET,
            api_passphrase=CLOB_API_PASSPHRASE,
        )
        _client = ClobClient(host, key=PRIVATE_KEY, chain_id=POLYGON, creds=creds)
        print("[TRADER] Initialized with saved API creds")
    else:
        # Slow path: derive creds from private key
        _client = ClobClient(host, key=PRIVATE_KEY, chain_id=POLYGON, signature_type=0)
        creds = _client.create_or_derive_api_creds()
        _client.set_api_creds(creds)
        print(f"[TRADER] Derived API creds — save these for faster startup:")
        print(f"  CLOB_API_KEY={creds.api_key}")
        print(f"  CLOB_API_SECRET={creds.api_secret}")
        print(f"  CLOB_API_PASSPHRASE={creds.api_passphrase}")

    return _client


def require_auth(f):
    """Verify the shared API secret on incoming requests."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not API_SECRET:
            return jsonify({"error": "API_SECRET not configured on server"}), 500
        if auth != f"Bearer {API_SECRET}":
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "has_private_key": bool(PRIVATE_KEY),
        "has_api_secret": bool(API_SECRET),
        "has_clob_creds": bool(CLOB_API_KEY),
    })


@app.route("/balance", methods=["GET"])
@require_auth
def balance():
    """Return USDC collateral balance."""
    try:
        client = get_client()
        result = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return jsonify({
            "balance_usdc": result.get("balance", "0"),
            "allowance": result.get("allowance", "0"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/execute", methods=["POST"])
@require_auth
def execute():
    """Place a limit order on Polymarket.

    Expected JSON body:
    {
        "trade_id": "uuid",
        "token_id": "0x...",
        "side": "BUY",
        "price": 0.06,
        "size": 83.0,
        "order_type": "GTC",
        "portfolio_id": "uuid"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "no JSON body"}), 400

        token_id = data.get("token_id")
        side_str = data.get("side", "BUY").upper()
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))
        order_type = data.get("order_type", "GTC").upper()

        if not token_id:
            return jsonify({"success": False, "error": "token_id required"}), 400
        if price <= 0 or price >= 1:
            return jsonify({"success": False, "error": f"invalid price: {price}"}), 400
        if size <= 0:
            return jsonify({"success": False, "error": f"invalid size: {size}"}), 400

        client = get_client()
        side = BUY if side_str == "BUY" else SELL

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=round(size, 2),
            side=side,
        )

        # Map order type
        ot_map = {
            "GTC": OrderType.GTC,
            "FOK": OrderType.FOK,
            "GTD": OrderType.GTD,
        }
        ot = ot_map.get(order_type, OrderType.GTC)

        signed_order = client.create_order(order_args)
        result = client.post_order(signed_order, orderType=ot)

        # Calculate costs for the response
        net_cost_usd = round(price * size, 2)
        fees_usd = round(net_cost_usd * 0.0125, 2)

        print(f"[EXECUTE] {side_str} {size} shares @ {price} = ${net_cost_usd} "
              f"(fees: ${fees_usd}) token={token_id[:16]}...")

        return jsonify({
            "success": True,
            "order_id": result.get("orderID", result.get("order_id", "")),
            "status": result.get("status", ""),
            "net_cost_usd": net_cost_usd,
            "fees_usd": fees_usd,
            "total_cost_usd": round(net_cost_usd + fees_usd, 2),
            "result": result,
        })

    except Exception as e:
        print(f"[EXECUTE ERROR] {e}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


@app.route("/orders", methods=["GET"])
@require_auth
def orders():
    """List open orders."""
    try:
        client = get_client()
        result = client.get_orders(OpenOrderParams())
        return jsonify({"orders": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cancel", methods=["POST"])
@require_auth
def cancel():
    """Cancel an order or all orders.

    Body: {"order_id": "xxx"} to cancel one, or {"all": true} to cancel all.
    """
    try:
        data = request.get_json() or {}
        client = get_client()

        if data.get("all"):
            result = client.cancel_all()
            return jsonify({"cancelled": "all", "result": result})

        order_id = data.get("order_id")
        if not order_id:
            return jsonify({"error": "order_id or all=true required"}), 400

        result = client.cancel(order_id)
        return jsonify({"cancelled": order_id, "result": result})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/derive-creds", methods=["POST"])
@require_auth
def derive_creds():
    """Derive and return CLOB API creds. Call once, save the result."""
    try:
        if not PRIVATE_KEY:
            return jsonify({"error": "POLYMARKET_PRIVATE_KEY not set"}), 500

        host = "https://clob.polymarket.com"
        client = ClobClient(host, key=PRIVATE_KEY, chain_id=POLYGON, signature_type=0)
        creds = client.create_or_derive_api_creds()

        return jsonify({
            "api_key": creds.api_key,
            "api_secret": creds.api_secret,
            "api_passphrase": creds.api_passphrase,
            "note": "Save these as CLOB_API_KEY, CLOB_API_SECRET, CLOB_API_PASSPHRASE env vars",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
