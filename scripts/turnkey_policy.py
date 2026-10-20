"""Print the Turnkey policy for the employee's API user, built from the whitelist.

    python3 scripts/turnkey_policy.py --user-id <Turnkey API user id>

Paste the output into Turnkey dashboard → Policies → Create policy. Re-run and
update the policy whenever the whitelist in config/policy.json changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_chain, load_policy  # noqa: E402
from agent.execution.turnkey_policy import policy_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", required=True, help="Turnkey user id of the employee's (non-root) API user")
    ap.add_argument("--policy", default="config/policy.json")
    ap.add_argument("--chain", default="config/chain.json")
    ap.add_argument("--token", default="USDT")
    args = ap.parse_args(argv)
    pol, _ = load_policy(args.policy)
    chain = load_chain(args.chain)
    token = chain.tokens.get(args.token)
    if token is None or token.is_native:
        print(f"{args.token} 在 {args.chain} 里没有合约地址", file=sys.stderr)
        return 2
    print(policy_json(chain.chain_id, token.address, pol.whitelist.keys(), args.user_id))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
