"""Wire the employee together from environment variables. Shared by the bot and the console.

CHAINSTAFF_POLICY   rules file (default config/policy.json, falls back to the example)
CHAINSTAFF_DB       SQLite ledger (default data/chainstaff.db)
CHAINSTAFF_CHAIN    chain settings, e.g. config/chain.example.json (optional)
CHAINSTAFF_LIVE     "1" to send real transactions on the configured chain; anything else = test mode
CHAINSTAFF_WALLET   wallet backend for live mode: turnkey | evm (bnbagent SDK).
                    twak can't sign raw transactions, so it can't send token payments.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.config import ChainConfig, PolicyFile, load_chain
from agent.execution import DraftSafeProposer, DryRunExecutor, RpcSender, WalletExecutor
from agent.execution.rpc import RpcClient
from agent.skills.finance_ops.employee import FinanceOpsEmployee
from agent.store import Store

MAINNET_CHAIN_ID = 56


def policy_path() -> Path:
    p = Path(os.environ.get("CHAINSTAFF_POLICY", "config/policy.json"))
    return p if p.exists() else Path("config/policy.example.json")


def make_wallet(kind: str):  # pragma: no cover - needs bnbagent and a configured wallet
    if kind == "turnkey":
        # Production choice: key stays in Turnkey's enclave; limits live in Turnkey policies.
        from bnbagent.wallets import TurnkeyWalletProvider

        return TurnkeyWalletProvider.from_env()
    if kind == "evm":
        # Testnet only: encrypted local keystore (~/.bnbagent/wallets).
        from bnbagent.wallets import create_wallet_provider

        return create_wallet_provider("evm", password=os.environ["CHAINSTAFF_WALLET_PASSWORD"])
    raise RuntimeError(f"不支持的钱包类型 {kind!r}：实盘付款请用 turnkey（主网）或 evm（测试网）")


def build_employee(env: dict[str, str] | None = None, wallet_factory=None, rpc_factory=None) -> tuple[FinanceOpsEmployee, ChainConfig | None]:
    env = dict(os.environ if env is None else env)
    db = Path(env.get("CHAINSTAFF_DB", "data/chainstaff.db"))
    if str(db) != ":memory:":
        db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db)
    policy_file = PolicyFile(env.get("CHAINSTAFF_POLICY") or policy_path())

    chain = load_chain(env["CHAINSTAFF_CHAIN"]) if env.get("CHAINSTAFF_CHAIN") else None
    executor = DryRunExecutor()
    proposer = None
    balance = None
    if chain:
        rpc = (rpc_factory or RpcClient)(chain.rpc_url)
        if env.get("CHAINSTAFF_LIVE") == "1":
            if rpc.chain_id() != chain.chain_id:
                raise RuntimeError(f"RPC 返回的链 ID 和配置不一致（配置 {chain.chain_id}），拒绝启动实盘")
            if chain.chain_id == MAINNET_CHAIN_ID and env.get("CHAINSTAFF_ALLOW_MAINNET") != "yes":
                raise RuntimeError("拒绝在 BSC 主网实盘运行：需要老板明确同意后设置 CHAINSTAFF_ALLOW_MAINNET=yes")
            if chain.chain_id == MAINNET_CHAIN_ID and env.get("CHAINSTAFF_WALLET") == "evm":
                raise RuntimeError("主网不允许用本地私钥钱包，请用 turnkey 并在 Turnkey 策略里配置白名单和限额")
            wallet = (wallet_factory or make_wallet)(env.get("CHAINSTAFF_WALLET", ""))
            executor = WalletExecutor(RpcSender(wallet, rpc, chain.chain_id), chain.tokens)
        if chain.safe_address:
            # Draft uses the Safe's current on-chain nonce; owners may need to re-queue if others are pending.
            proposer = DraftSafeProposer(chain.chain_id, chain.safe_address, chain.tokens, lambda: rpc.safe_nonce(chain.safe_address))
        if chain.treasury_addresses and chain.tokens:
            balance = lambda: rpc.total_balance(chain.tokens.values(), chain.treasury_addresses)  # noqa: E731

    employee = FinanceOpsEmployee(
        policy_file.load,
        store,
        executor=executor,
        proposer=proposer,
        explorer_tx_url=chain.explorer_tx_url if chain else None,
        treasury_balance=balance,
    )
    employee.policy_file = policy_file  # the console edits rules through this
    return employee, chain
