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
from wallet_manager import WalletManager

app = Flask(__name__)

# --- Config ---
PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

# Multi-wallet manager
wallet_mgr = WalletManager()

# Optional: saved CLOB API creds for faster startup
CLOB_API_KEY = os.environ.get("CLOB_API_KEY", "")
CLOB_API_SECRET = os.environ.get("CLOB_API_SECRET", "")
CLOB_API_PASSPHRASE = os.environ.get("CLOB_API_PASSPHRASE", "")

# Singleton client
_client = None


def get_client(wallet_address: str = None):
    """Get or create the authenticated CLOB client.

    If wallet_address is provided, returns that wallet's client from wallet_mgr.
    Otherwise falls back to the singleton client or wallet_mgr default.
    """
    # If a specific wallet is requested, use wallet_mgr
    if wallet_address:
        client = wallet_mgr.get_client(wallet_address)
        if client:
            return client
        raise RuntimeError(f"Wallet {wallet_address[:10]}... not registered")

    global _client
    if _client is not None:
        return _client

    # Try wallet_mgr default first
    mgr_client = wallet_mgr.get_default_client()
    if mgr_client:
        _client = mgr_client
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

        wallet_address = data.get("wallet_address")
        client = get_client(wallet_address)
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

        # Snapshot USDC balance before trade
        try:
            bal_before = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            usdc_before = float(bal_before.get("balance", "0")) / 1e6
        except Exception:
            usdc_before = None

        signed_order = client.create_order(order_args)
        result = client.post_order(signed_order, orderType=ot)

        order_id = result.get("orderID", result.get("order_id", ""))
        order_status = result.get("status", "")

        # Poll for fill data — wait briefly then check order + balance
        import time
        time.sleep(2)

        # Get actual fill details from the order
        actual_fill = None
        try:
            if order_id:
                order_info = client.get_order(order_id)
                actual_fill = {
                    "order_id": order_id,
                    "status": order_info.get("status", order_status),
                    "original_size": order_info.get("original_size"),
                    "size_matched": order_info.get("size_matched", "0"),
                    "price": order_info.get("price"),
                    "side": order_info.get("side"),
                    "associate_trades": order_info.get("associate_trades", []),
                }
        except Exception as fill_err:
            actual_fill = {"error": str(fill_err), "order_id": order_id}

        # Snapshot USDC balance after trade
        try:
            bal_after = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            usdc_after = float(bal_after.get("balance", "0")) / 1e6
        except Exception:
            usdc_after = None

        # Calculate actual cost from balance change
        actual_cost = None
        if usdc_before is not None and usdc_after is not None:
            actual_cost = round(usdc_before - usdc_after, 6)

        # Estimated cost as fallback
        estimated_cost = round(price * size, 2)

        print(f"[EXECUTE] {side_str} {size} shares @ {price} "
              f"estimated=${estimated_cost} actual=${actual_cost} "
              f"status={order_status} token={token_id[:16]}...")

        return jsonify({
            "success": True,
            "order_id": order_id,
            "status": order_status,
            "estimated_cost_usd": estimated_cost,
            "actual_cost_usd": actual_cost,
            "usdc_before": usdc_before,
            "usdc_after": usdc_after,
            "fill_data": actual_fill,
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


# --- Wallet management endpoints ---

@app.route("/wallets/register", methods=["POST"])
@require_auth
def register_wallet():
    """Register a new wallet. Body: {address, private_key, label}"""
    data = request.get_json() or {}
    address = data.get("address", "")
    private_key = data.get("private_key", "")
    label = data.get("label", "")
    if not address or not private_key:
        return jsonify({"error": "address and private_key required"}), 400
    try:
        wallet_mgr.register_wallet(address, private_key, label)
        return jsonify({"registered": True, "address": address, "label": label})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wallets/list", methods=["GET"])
@require_auth
def list_wallets():
    """List all wallets (no keys exposed)."""
    return jsonify({"wallets": wallet_mgr.list_wallets()})


@app.route("/wallets/balance", methods=["GET"])
@require_auth
def wallet_balance():
    """Get balance for a specific wallet. Query: ?address=0x..."""
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "address required"}), 400
    try:
        bal = wallet_mgr.get_balance(address)
        return jsonify({"address": address, **bal})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wallets/remove", methods=["POST"])
@require_auth
def remove_wallet():
    """Remove a wallet. Body: {address}"""
    data = request.get_json() or {}
    address = data.get("address", "")
    if not address:
        return jsonify({"error": "address required"}), 400
    wallet_mgr.remove_wallet(address)
    return jsonify({"removed": True, "address": address})


# --- Shared web3 helpers ---

# Token addresses
USDC_E_ADDR = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE_ADDR = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# Polymarket contract addresses
POLYMARKET_SPENDERS = {
    "CTF Exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "Neg Risk Exchange": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "Neg Risk Adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]


def get_web3():
    """Connect to Polygon via the first working RPC."""
    from web3 import Web3

    rpc_urls = [
        os.environ.get("POLYGON_RPC_URL", ""),
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.drpc.org",
        "https://1rpc.io/matic",
    ]
    for rpc_url in rpc_urls:
        if not rpc_url:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
            w3.eth.block_number
            return w3, rpc_url
        except Exception:
            continue
    return None, None


@app.route("/set-allowances", methods=["POST"])
@require_auth
def set_allowances():
    """Approve Polymarket contracts to spend USDC.e using raw web3 with debug info."""
    try:
        w3, connected_rpc = get_web3()
        if w3 is None:
            return jsonify({"error": "Cannot connect to any Polygon RPC"}), 500

        account = w3.eth.account.from_key(PRIVATE_KEY)
        address = account.address

        usdc_e = w3.to_checksum_address(USDC_E_ADDR)

        # Full USDC.e ABI with approve
        usdc_contract = w3.eth.contract(address=usdc_e, abi=ERC20_ABI)

        # Check current state
        balance = usdc_contract.functions.balanceOf(address).call()

        spenders = {k: w3.to_checksum_address(v) for k, v in POLYMARKET_SPENDERS.items()}

        results = []
        for name, spender in spenders.items():
            current = usdc_contract.functions.allowance(address, spender).call()

            # Try to estimate gas first to see if it would revert
            try:
                gas_est = usdc_contract.functions.approve(spender, 2**256 - 1).estimate_gas({"from": address})
                estimation = {"gas_estimate": gas_est}
            except Exception as est_err:
                estimation = {"gas_estimate_error": str(est_err)}

            # Try sending with higher gas
            try:
                nonce = w3.eth.get_transaction_count(address, "pending")
                tx = usdc_contract.functions.approve(spender, 2**256 - 1).build_transaction({
                    "from": address, "nonce": nonce,
                    "gasPrice": w3.eth.gas_price, "gas": 200000, "chainId": 137,
                })
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

                new_allowance = usdc_contract.functions.allowance(address, spender).call()

                results.append({
                    "spender": name,
                    "current_allowance_before": str(current),
                    "tx_status": receipt["status"],
                    "gas_used": receipt["gasUsed"],
                    "tx_hash": tx_hash.hex(),
                    "new_allowance": str(new_allowance),
                    **estimation,
                })
            except Exception as tx_err:
                results.append({
                    "spender": name,
                    "current_allowance_before": str(current),
                    "error": str(tx_err),
                    **estimation,
                })

        # Final CLOB balance check
        client = get_client()
        clob_bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )

        return jsonify({
            "address": address,
            "rpc": connected_rpc,
            "usdc_e_balance_raw": str(balance),
            "results": results,
            "clob_balance_after": clob_bal,
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/approve", methods=["POST"])
@require_auth
def approve():
    """Approve Polymarket exchange contracts to spend USDC.e and native USDC."""
    try:
        w3, connected_rpc = get_web3()
        if w3 is None:
            return jsonify({"error": "Cannot connect to any Polygon RPC"}), 500

        account = w3.eth.account.from_key(PRIVATE_KEY)
        address = account.address

        tokens = [
            (w3.to_checksum_address(USDC_E_ADDR), "USDC.e"),
            (w3.to_checksum_address(USDC_NATIVE_ADDR), "USDC"),
        ]
        spenders = {k: w3.to_checksum_address(v) for k, v in POLYMARKET_SPENDERS.items()}

        MAX_UINT256 = 2**256 - 1
        results = []

        for token_addr, token_name in tokens:
            contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)

            for spender_name, spender_addr in spenders.items():
                current_allowance = contract.functions.allowance(address, spender_addr).call()

                if current_allowance >= MAX_UINT256 // 2:
                    results.append({"token": token_name, "spender": spender_name, "status": "already_approved"})
                    continue

                nonce = w3.eth.get_transaction_count(address, "pending")
                tx = contract.functions.approve(spender_addr, MAX_UINT256).build_transaction({
                    "from": address, "nonce": nonce,
                    "gasPrice": w3.eth.gas_price, "gas": 60000, "chainId": 137,
                })
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

                results.append({
                    "token": token_name, "spender": spender_name,
                    "status": "approved" if receipt["status"] == 1 else "failed",
                    "tx_hash": tx_hash.hex(),
                })

        usdc_e = w3.eth.contract(address=w3.to_checksum_address(USDC_E_ADDR), abi=ERC20_ABI)
        usdc_n = w3.eth.contract(address=w3.to_checksum_address(USDC_NATIVE_ADDR), abi=ERC20_ABI)

        return jsonify({
            "address": address, "rpc": connected_rpc, "approvals": results,
            "balances": {
                "usdc_e": str(usdc_e.functions.balanceOf(address).call() / 1e6),
                "usdc_native": str(usdc_n.functions.balanceOf(address).call() / 1e6),
                "pol": str(w3.eth.get_balance(address) / 1e18),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/swap", methods=["POST"])
@require_auth
def swap():
    """Swap native USDC to USDC.e via Uniswap V3 on Polygon.

    Body: {"amount_usdc": 49.0}  (how much native USDC to swap)
    Omit amount to swap entire native USDC balance.
    """
    try:
        w3, connected_rpc = get_web3()
        if w3 is None:
            return jsonify({"error": "Cannot connect to any Polygon RPC"}), 500

        account = w3.eth.account.from_key(PRIVATE_KEY)
        address = account.address

        usdc_native = w3.to_checksum_address(USDC_NATIVE_ADDR)
        usdc_e = w3.to_checksum_address(USDC_E_ADDR)

        usdc_contract = w3.eth.contract(address=usdc_native, abi=ERC20_ABI)
        balance_raw = usdc_contract.functions.balanceOf(address).call()
        balance_usdc = balance_raw / 1e6

        data = request.get_json() or {}
        amount_usdc = float(data.get("amount_usdc", balance_usdc))

        if amount_usdc <= 0:
            return jsonify({"error": "No amount to swap"}), 400
        if amount_usdc > balance_usdc:
            return jsonify({"error": f"Insufficient balance: have ${balance_usdc}, want ${amount_usdc}"}), 400

        amount_raw = int(amount_usdc * 1e6)

        # Uniswap V3 SwapRouter02 on Polygon
        SWAP_ROUTER = w3.to_checksum_address("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45")

        # Step 1: Approve SwapRouter to spend native USDC
        current_allowance = usdc_contract.functions.allowance(address, SWAP_ROUTER).call()
        approvals = []
        if current_allowance < amount_raw:
            nonce = w3.eth.get_transaction_count(address, "pending")
            approve_tx = usdc_contract.functions.approve(SWAP_ROUTER, 2**256 - 1).build_transaction({
                "from": address, "nonce": nonce,
                "gasPrice": w3.eth.gas_price, "gas": 60000, "chainId": 137,
            })
            signed = account.sign_transaction(approve_tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            approvals.append({"status": "approved" if receipt["status"] == 1 else "failed", "tx_hash": tx_hash.hex()})

        # Step 2: Swap via exactInputSingle
        # Uniswap V3 SwapRouter02 exactInputSingle ABI
        swap_abi = [{"inputs":[{"components":[
            {"name":"tokenIn","type":"address"},
            {"name":"tokenOut","type":"address"},
            {"name":"fee","type":"uint24"},
            {"name":"recipient","type":"address"},
            {"name":"amountIn","type":"uint256"},
            {"name":"amountOutMinimum","type":"uint256"},
            {"name":"sqrtPriceLimitX96","type":"uint160"}
        ],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]

        router = w3.eth.contract(address=SWAP_ROUTER, abi=swap_abi)

        # USDC/USDC.e should be ~1:1, set 0.5% slippage
        min_out = int(amount_raw * 0.995)

        # Try fee tiers: 100 (0.01%), 500 (0.05%)
        swap_result = None
        for fee_tier in [100, 500]:
            try:
                nonce = w3.eth.get_transaction_count(address, "pending")
                swap_tx = router.functions.exactInputSingle((
                    usdc_native,    # tokenIn
                    usdc_e,         # tokenOut
                    fee_tier,       # fee
                    address,        # recipient
                    amount_raw,     # amountIn
                    min_out,        # amountOutMinimum
                    0,              # sqrtPriceLimitX96 (0 = no limit)
                )).build_transaction({
                    "from": address, "nonce": nonce,
                    "gasPrice": w3.eth.gas_price, "gas": 300000, "chainId": 137,
                    "value": 0,
                })
                signed = account.sign_transaction(swap_tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)

                if receipt["status"] == 1:
                    swap_result = {
                        "status": "success",
                        "fee_tier": fee_tier,
                        "tx_hash": tx_hash.hex(),
                        "gas_used": receipt["gasUsed"],
                    }
                    break
                else:
                    swap_result = {"status": "reverted", "fee_tier": fee_tier, "tx_hash": tx_hash.hex()}
            except Exception as swap_err:
                swap_result = {"status": "failed", "fee_tier": fee_tier, "error": str(swap_err)}
                continue

        # Check final balances
        usdc_e_contract = w3.eth.contract(address=usdc_e, abi=ERC20_ABI)
        final_usdc_e = usdc_e_contract.functions.balanceOf(address).call() / 1e6
        final_usdc_native = usdc_contract.functions.balanceOf(address).call() / 1e6

        return jsonify({
            "address": address,
            "rpc": connected_rpc,
            "swapped_amount": amount_usdc,
            "approvals": approvals,
            "swap": swap_result,
            "balances_after": {
                "usdc_e": str(final_usdc_e),
                "usdc_native": str(final_usdc_native),
                "pol": str(w3.eth.get_balance(address) / 1e18),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ---------------------------------------------------------------------------
# Trading loop (background thread)
# ---------------------------------------------------------------------------
from trading_loop import TradingLoop

_trading_loop = TradingLoop(app)
_trading_loop.start()


@app.route("/loop-status", methods=["GET"])
@require_auth
def loop_status():
    return jsonify(_trading_loop.status())


@app.route("/loop-control", methods=["POST"])
@require_auth
def loop_control():
    data = request.get_json() or {}
    action = data.get("action")
    if action == "stop":
        _trading_loop.stop()
    elif action == "start":
        _trading_loop.start()
    elif action == "pause":
        _trading_loop.pause()
    return jsonify(_trading_loop.status())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
