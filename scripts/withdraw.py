"""Send money from the employee wallet back to the owner (e.g. leftover BNB or USDT).

    python3 scripts/withdraw.py --to 0xYourWallet --amount 0.003 --token BNB

Uses the same wallet and chain settings as the bot (CHAINSTAFF_CHAIN, CHAINSTAFF_WALLET,
TURNKEY_*). On Turnkey the signing policy decides whether the transfer is allowed:
BNB needs the owner-BNB policy from `--print-policy`.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_chain  # noqa: E402
from agent.execution import RpcSender, WalletExecutor  # noqa: E402
from agent.execution.rpc import RpcClient  # noqa: E402
from agent.execution.tokens import TokenInfo  # noqa: E402
from agent.execution.turnkey_policy import owner_bnb_condition  # noqa: E402
from agent.rules import PaymentRequest  # noqa: E402
from agent.runtime import MAINNET_CHAIN_ID, make_wallet  # noqa: E402


def main(argv=None, env=None, wallet_factory=None, rpc_factory=None, confirm=input) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="owner's own wallet address")
    ap.add_argument("--amount", help="amount in BNB or USDT, e.g. 0.003")
    ap.add_argument("--token", default="BNB")
    ap.add_argument("--print-policy", action="store_true", help="print the Turnkey policy condition that allows BNB to --to")
    args = ap.parse_args(argv)
    env = dict(os.environ if env is None else env)
    chain = load_chain(env.get("CHAINSTAFF_CHAIN", "config/chain.json"))

    if args.print_policy:
        print(owner_bnb_condition(chain.chain_id, args.to))
        return 0
    if not args.amount:
        print("需要 --amount", file=sys.stderr)
        return 2
    if chain.chain_id == MAINNET_CHAIN_ID and env.get("CHAINSTAFF_ALLOW_MAINNET") != "yes":
        print("主网转账需要设置 CHAINSTAFF_ALLOW_MAINNET=yes", file=sys.stderr)
        return 2
    tokens = dict(chain.tokens)
    tokens.setdefault("BNB", TokenInfo("BNB", 18, None))
    if args.token not in tokens:
        print(f"{args.token} 在 {chain.network} 上没有配置", file=sys.stderr)
        return 2

    rpc = (rpc_factory or RpcClient)(chain.rpc_url)
    if rpc.chain_id() != chain.chain_id:
        print(f"RPC 返回的链 ID 和配置不一致（配置 {chain.chain_id}）", file=sys.stderr)
        return 2
    if confirm(f"从员工钱包转 {args.amount} {args.token} 到 {args.to}（{chain.network}），输入 yes 确认：").strip() != "yes":
        print("已取消")
        return 1
    kind = env.get("CHAINSTAFF_WALLET", "")
    wallet = wallet_factory(kind) if wallet_factory else make_wallet(kind, chain.chain_id)
    executor = WalletExecutor(RpcSender(wallet, rpc, chain.chain_id), tokens)
    req = PaymentRequest("withdraw", "owner", args.to, Decimal(args.amount), args.token, datetime.now(timezone.utc))
    tx = executor.pay(req)
    print(f"已发出：{chain.explorer_tx_url or ''}{tx}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
