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


def make_wallet(kind: str, chain_id: int | None = None):  # pragma: no cover - needs bnbagent and a configured wallet
    if kind == "turnkey":
        # Production choice: key stays in Turnkey's enclave; limits live in Turnkey policies.
        # Pinned to the configured chain, so it never signs for another network.
        from bnbagent.wallets import TurnkeyWalletProvider

        return TurnkeyWalletProvider.from_env(expected_chain_id=chain_id)
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
            kind = env.get("CHAINSTAFF_WALLET", "")
            wallet = wallet_factory(kind) if wallet_factory else make_wallet(kind, chain.chain_id)
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


def build_front_desk(env: dict[str, str] | None = None, wallet_factory=None, rpc_factory=None, anthropic_factory=None):
    """The whole AI team on one ledger: finance ops plus support, chain monitor and translator.

    ETHERSCAN_API_KEY   lets the chain monitor and the sybil checker read the chain (optional)
    ANTHROPIC_API_KEY   lets the announcement translator draft translations (optional)
    """
    from agent.front_desk import FrontDesk
    from agent.skills.airdrop_sybil.sybil import EtherscanProfileSource, SybilChecker
    from agent.skills.campaign_payouts.payouts import CampaignPayouts
    from agent.skills.chain_monitor.monitor import ChainMonitor
    from agent.skills.community_support.support import CommunitySupport
    from agent.skills.finance_ops.alerts import EtherscanV2Source
    from agent.skills.translator.translator import Translator

    env = dict(os.environ if env is None else env)
    employee, chain = build_employee(env, wallet_factory, rpc_factory)
    store = employee.store
    tx_lookup = source = balances = profiles = None
    if chain:
        rpc = (rpc_factory or RpcClient)(chain.rpc_url)
        tx_lookup = rpc.tx_status
        if chain.tokens:
            balances = lambda a: {t.symbol: rpc.balance(t, a) for t in chain.tokens.values()}  # noqa: E731
        if chain.explorer_api_url and env.get("ETHERSCAN_API_KEY"):
            source = EtherscanV2Source(chain.explorer_api_url, env["ETHERSCAN_API_KEY"], {s: t.address for s, t in chain.tokens.items()})
            profiles = EtherscanProfileSource(chain.explorer_api_url, env["ETHERSCAN_API_KEY"])
    client_factory = None
    if env.get("ANTHROPIC_API_KEY"):
        client_factory = anthropic_factory or _anthropic_client  # pragma: no branch
    support = CommunitySupport(store, tx_lookup, chain.explorer_tx_url if chain else None)
    monitor = ChainMonitor(
        store,
        source,
        balances,
        known=lambda: list(employee.policy.whitelist),
        default_watch=chain.treasury_addresses if chain else (),
        default_thresholds=chain.alert_outflow_threshold if chain else None,
        tokens={t.address: s for s, t in chain.tokens.items() if t.address} if chain else None,
        explorer_tx_url=chain.explorer_tx_url if chain else None,
        timezone_name=employee.policy.timezone,
    )
    payouts = CampaignPayouts(
        store,
        lambda: employee.policy,
        tokens=dict(chain.tokens) if chain else None,
        chain_id=chain.chain_id if chain else None,
        safe_address=chain.safe_address if chain else None,
        explorer_tx_url=chain.explorer_tx_url if chain else None,
    )
    sybil = SybilChecker(store, profiles, risk=lambda: employee.policy.risk_addresses)
    desk = FrontDesk(employee, support, monitor, Translator(store, client_factory), payouts=payouts, sybil=sybil)
    return desk, chain


def _anthropic_client():  # pragma: no cover - real network client
    import anthropic

    return anthropic.Anthropic()
