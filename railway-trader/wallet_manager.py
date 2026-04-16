"""
Multi-wallet manager for PolyWeather Railway trader.

Stores multiple wallets in memory, initializes ClobClients for each,
persists wallet keys to a local JSON file + reads from WALLET_KEYS env var.
Never exposes private keys in API responses or logs.
"""
import os
import json
import threading
from datetime import datetime, timezone

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from py_clob_client.constants import POLYGON

WALLET_FILE = "/app/wallet_keys.json"
HOST = "https://clob.polymarket.com"

# ERC20 ABI for balance checks
ERC20_BALANCE_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

USDC_E_ADDR = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE_ADDR = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"


class WalletManager:
    """Manages multiple Polymarket wallets with ClobClient instances."""

    def __init__(self):
        self._lock = threading.Lock()
        # {address: {private_key, clob_client, label, created_at}}
        self._wallets: dict = {}
        self._default_address: str | None = None
        self._load_wallets()

    # --- Startup loading ---

    def _load_wallets(self):
        """Load wallets from env vars and local file on startup."""
        # 1. Load default wallet from POLYMARKET_PRIVATE_KEY
        default_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        if default_key:
            try:
                client = self._init_client(default_key)
                # Derive address from the client (the client stores it internally)
                address = self._address_from_key(default_key)
                self._wallets[address] = {
                    "private_key": default_key,
                    "clob_client": client,
                    "label": "Default",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self._default_address = address
                print(f"[WALLET_MGR] Loaded default wallet: {address[:10]}...")
            except Exception as e:
                print(f"[WALLET_MGR] Failed to load default wallet: {e}")

        # 2. Load from WALLET_KEYS env var
        env_keys = os.environ.get("WALLET_KEYS", "")
        if env_keys:
            try:
                parsed = json.loads(env_keys)
                self._load_from_dict(parsed, source="env")
            except Exception as e:
                print(f"[WALLET_MGR] Failed to parse WALLET_KEYS env: {e}")

        # 3. Load from local file
        if os.path.exists(WALLET_FILE):
            try:
                with open(WALLET_FILE, "r") as f:
                    parsed = json.load(f)
                self._load_from_dict(parsed, source="file")
            except Exception as e:
                print(f"[WALLET_MGR] Failed to load {WALLET_FILE}: {e}")

        print(f"[WALLET_MGR] {len(self._wallets)} wallet(s) loaded")

    def _load_from_dict(self, data: dict, source: str = ""):
        """Load wallets from a dict like {"0xabc": {"key": "...", "label": "..."}}.
        Skips wallets already loaded (no overwrite)."""
        for address, info in data.items():
            addr = address.lower() if not address.startswith("0x") else address
            if addr in self._wallets:
                continue
            key = info.get("key", "")
            label = info.get("label", "")
            if not key:
                continue
            try:
                client = self._init_client(key)
                self._wallets[addr] = {
                    "private_key": key,
                    "clob_client": client,
                    "label": label,
                    "created_at": info.get("created_at", datetime.now(timezone.utc).isoformat()),
                }
                if self._default_address is None:
                    self._default_address = addr
                print(f"[WALLET_MGR] Loaded wallet from {source}: {addr[:10]}... ({label})")
            except Exception as e:
                print(f"[WALLET_MGR] Failed to init wallet {addr[:10]}... from {source}: {e}")

    def _init_client(self, private_key: str) -> ClobClient:
        """Initialize a ClobClient for a private key, deriving creds."""
        client = ClobClient(HOST, key=private_key, chain_id=POLYGON, signature_type=0)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client

    def _address_from_key(self, private_key: str) -> str:
        """Derive the Ethereum address from a private key."""
        try:
            from eth_account import Account
            acct = Account.from_key(private_key)
            return acct.address
        except ImportError:
            # Fallback: use web3
            from web3 import Web3
            w3 = Web3()
            acct = w3.eth.account.from_key(private_key)
            return acct.address

    # --- Persistence ---

    def _persist(self):
        """Save current wallets to local JSON file (no keys logged)."""
        data = {}
        for addr, info in self._wallets.items():
            data[addr] = {
                "key": info["private_key"],
                "label": info.get("label", ""),
                "created_at": info.get("created_at", ""),
            }
        try:
            os.makedirs(os.path.dirname(WALLET_FILE), exist_ok=True)
            with open(WALLET_FILE, "w") as f:
                json.dump(data, f)
            print(f"[WALLET_MGR] Persisted {len(data)} wallet(s) to {WALLET_FILE}")
        except Exception as e:
            print(f"[WALLET_MGR] Failed to persist wallets: {e}")

    # --- Public methods ---

    def register_wallet(self, address: str, private_key: str, label: str = ""):
        """Add a new wallet, initialize its ClobClient, persist."""
        with self._lock:
            # Verify the key matches the address
            derived = self._address_from_key(private_key)
            if derived.lower() != address.lower():
                raise ValueError(f"Private key does not match address. Key derives to {derived[:10]}...")

            client = self._init_client(private_key)
            self._wallets[address] = {
                "private_key": private_key,
                "clob_client": client,
                "label": label,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if self._default_address is None:
                self._default_address = address
            self._persist()
            print(f"[WALLET_MGR] Registered wallet: {address[:10]}... ({label})")

    def get_client(self, address: str) -> ClobClient | None:
        """Return the ClobClient for a wallet address, or None."""
        info = self._wallets.get(address)
        return info["clob_client"] if info else None

    def get_default_client(self) -> ClobClient | None:
        """Return the ClobClient for the default/first wallet."""
        if self._default_address:
            return self.get_client(self._default_address)
        # Fallback: return first available
        if self._wallets:
            first = next(iter(self._wallets))
            return self._wallets[first]["clob_client"]
        return None

    def get_default_address(self) -> str | None:
        """Return the default wallet address."""
        return self._default_address

    def get_balance(self, address: str) -> dict:
        """Return USDC.e and CLOB balances for a wallet."""
        info = self._wallets.get(address)
        if not info:
            raise ValueError(f"Wallet {address[:10]}... not registered")

        result = {"address": address}

        # CLOB balance via client
        try:
            client = info["clob_client"]
            bal = client.get_balance_allowance(
                params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            result["clob_balance_usdc"] = bal.get("balance", "0")
            result["clob_allowances"] = bal.get("allowances", {})
        except Exception as e:
            result["clob_balance_error"] = str(e)

        # On-chain balance via web3
        try:
            from web3 import Web3
            rpc_urls = [
                os.environ.get("POLYGON_RPC_URL", ""),
                "https://polygon-bor-rpc.publicnode.com",
                "https://polygon.drpc.org",
            ]
            w3 = None
            for rpc in rpc_urls:
                if not rpc:
                    continue
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
                    w3.eth.block_number  # test connection
                    break
                except Exception:
                    w3 = None
                    continue

            if w3:
                usdc_e = w3.eth.contract(
                    address=w3.to_checksum_address(USDC_E_ADDR),
                    abi=ERC20_BALANCE_ABI,
                )
                usdc_n = w3.eth.contract(
                    address=w3.to_checksum_address(USDC_NATIVE_ADDR),
                    abi=ERC20_BALANCE_ABI,
                )
                checksum = w3.to_checksum_address(address)
                result["usdc_e_balance"] = str(usdc_e.functions.balanceOf(checksum).call() / 1e6)
                result["usdc_native_balance"] = str(usdc_n.functions.balanceOf(checksum).call() / 1e6)
                result["pol_balance"] = str(w3.eth.get_balance(checksum) / 1e18)
        except Exception as e:
            result["onchain_balance_error"] = str(e)

        return result

    def list_wallets(self) -> list[dict]:
        """Return list of wallets with address, label, has_client. Never exposes keys."""
        wallets = []
        for addr, info in self._wallets.items():
            wallets.append({
                "address": addr,
                "label": info.get("label", ""),
                "has_client": info.get("clob_client") is not None,
                "is_default": addr == self._default_address,
                "created_at": info.get("created_at", ""),
            })
        return wallets

    def remove_wallet(self, address: str):
        """Remove a wallet from memory and persist."""
        with self._lock:
            if address in self._wallets:
                del self._wallets[address]
                if self._default_address == address:
                    self._default_address = next(iter(self._wallets), None)
                self._persist()
                print(f"[WALLET_MGR] Removed wallet: {address[:10]}...")
            else:
                print(f"[WALLET_MGR] Wallet not found for removal: {address[:10]}...")
