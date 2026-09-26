import re
from urllib.parse import quote

NANSEN_APP_URL = "https://app.nansen.ai"

_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BASE58_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

_EVM_EXPLORERS = {
    "ethereum": "https://etherscan.io",
    "base": "https://basescan.org",
    "bnb": "https://bscscan.com",
    "bsc": "https://bscscan.com",  # the name Nansen returns for BNB Chain tokens
    "robinhood": "https://robinhoodchain.blockscout.com",  # mainnet, per docs.robinhood.com
}
_SOLANA_EXPLORER = "https://solscan.io"


def is_valid_address(chain: str, address: str) -> bool:
    if chain == "solana":
        return bool(_BASE58_ADDRESS.match(address))
    if chain in _EVM_EXPLORERS:
        return bool(_EVM_ADDRESS.match(address))
    return False


def token_explorer_url(chain: str, address: str) -> str | None:
    """Block explorer page for a token, or None for an unknown chain or malformed address."""
    if not is_valid_address(chain, address):
        return None
    if chain == "solana":
        return f"{_SOLANA_EXPLORER}/token/{quote(address)}"
    return f"{_EVM_EXPLORERS[chain]}/token/{quote(address)}"


def wallet_explorer_url(chain: str, address: str) -> str | None:
    if not is_valid_address(chain, address):
        return None
    if chain == "solana":
        return f"{_SOLANA_EXPLORER}/account/{quote(address)}"
    return f"{_EVM_EXPLORERS[chain]}/address/{quote(address)}"
