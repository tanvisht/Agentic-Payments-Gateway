"""Thin EVM client over web3.py for the escrow rail.

Works against any JSON-RPC node (Base, Ethereum, a testnet, anvil) or, in
tests, the in-process py-evm backend via eth-tester, which executes the real
compiled bytecode from contracts/artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ARTIFACTS = Path(__file__).resolve().parents[2] / "contracts" / "artifacts"


def artifact(name: str) -> dict[str, Any]:
    return json.loads((ARTIFACTS / f"{name}.json").read_text())


class EvmChain:
    def __init__(self, w3, *, operator: str) -> None:
        self.w3 = w3
        self.operator = operator  # gateway account that signs release/refund

    @classmethod
    def in_memory(cls) -> "EvmChain":
        from web3 import EthereumTesterProvider, Web3

        w3 = Web3(EthereumTesterProvider())
        return cls(w3, operator=w3.eth.accounts[0])

    @property
    def accounts(self) -> list[str]:
        return list(self.w3.eth.accounts)

    def deploy(self, name: str, *args: Any, sender: str | None = None):
        a = artifact(name)
        c = self.w3.eth.contract(abi=a["abi"], bytecode=a["bytecode"])
        tx = c.constructor(*args).transact({"from": sender or self.operator})
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx)
        return self.w3.eth.contract(address=rcpt.contractAddress, abi=a["abi"])

    def send(self, fn, sender: str):
        tx = fn.transact({"from": sender})
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx)
        if rcpt.status != 1:
            raise RuntimeError("transaction reverted")
        return rcpt

    def now(self) -> int:
        return int(self.w3.eth.get_block("latest")["timestamp"])

    def advance_time(self, seconds: int) -> None:
        """Test helper (eth-tester only)."""
        tester = self.w3.provider.ethereum_tester
        tester.time_travel(self.now() + seconds)
        tester.mine_blocks(1)
