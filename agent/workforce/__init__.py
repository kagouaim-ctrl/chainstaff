"""AI workforce: agents you create, a manager that splits goals, a persisted message bus and task
graph, read-only on-chain skills, human approvals, and result hashes anchored on BSC testnet."""

from .engine import Workforce, WorkforceError

__all__ = ["Workforce", "WorkforceError", "build_workforce"]


def build_workforce(store, chain=None, env=None, rpc_factory=None, anthropic_factory=None, wallet_factory=None, http_get=None):
    """Assemble the workforce from the same environment the bot and console use.

    CHAINSTAFF_CHAIN           chain settings; without it, on-chain skills report that no chain is configured
    ETHERSCAN_API_KEY          enables the transaction-history skill and verified-source lookups
    ANTHROPIC_API_KEY          lets managers with provider=anthropic plan with a model
    CHAINSTAFF_MODEL_PRICE     "input,output" USD per million tokens, for cost tracking and budgets
    CHAINSTAFF_ANCHOR_WALLET   "evm" (bnbagent keystore, testnet only) to anchor approved result hashes
    """
    import os
    from decimal import Decimal

    from .anchor import Anchor
    from .chain import ChainReader

    env = dict(os.environ if env is None else env)
    reader = etherscan = anchor = None
    tokens = {}
    if chain:
        if rpc_factory is None:
            from agent.bsc.rpc import RpcPool

            # Several endpoints with failover, retries, rate limits and a short cache; still a plain RpcClient to callers.
            rpc_factory = lambda url: RpcPool(chain.http_endpoints, chain_id=chain.chain_id)  # noqa: E731
        rpc = rpc_factory(chain.rpc_url)
        reader = ChainReader(rpc, chain.network, chain.chain_id, chain.explorer_tx_url)
        tokens = {s: (t.address, t.decimals) for s, t in chain.tokens.items() if t.address}
        if chain.explorer_api_url and env.get("ETHERSCAN_API_KEY"):
            if http_get is None:  # pragma: no cover - real network
                import httpx

                http_get = lambda url, params: httpx.get(url, params=params, timeout=20).json()  # noqa: E731
            key, url = env["ETHERSCAN_API_KEY"], chain.explorer_api_url
            etherscan = lambda params: http_get(url, {**params, "apikey": key})  # noqa: E731
        if env.get("CHAINSTAFF_ANCHOR_WALLET"):
            from agent.execution import RpcSender

            if wallet_factory is None:  # pragma: no cover - needs a real keystore
                from agent.runtime import make_wallet

                wallet_factory = lambda kind: make_wallet(kind, chain.chain_id)  # noqa: E731
            wallet = wallet_factory(env["CHAINSTAFF_ANCHOR_WALLET"])
            anchor = Anchor(RpcSender(wallet, rpc, chain.chain_id), reader, wallet.address)
    llm = None
    if env.get("ANTHROPIC_API_KEY"):
        llm = anthropic_factory
        if llm is None:  # pragma: no cover - real network client
            import anthropic

            llm = anthropic.Anthropic
    price = None
    if env.get("CHAINSTAFF_MODEL_PRICE"):
        a, b = env["CHAINSTAFF_MODEL_PRICE"].split(",")
        price = (Decimal(a.strip()), Decimal(b.strip()))
    return Workforce(store, reader=reader, etherscan=etherscan, tokens=tokens, llm=llm, anchor=anchor, price_per_mtok=price)
