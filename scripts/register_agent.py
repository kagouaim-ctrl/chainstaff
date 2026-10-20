"""Register the ChainStaff employee's on-chain identity (ERC-8004) with the bnbagent SDK.

BSC testnet registration is gas-free (MegaFuel paymaster), so a new wallet works without tBNB.

    pip install bnbagent
    export WALLET_PASSWORD=...        # encrypts the local keystore
    export PRIVATE_KEY=0x...          # only on first run; omit to generate a new testnet wallet
    python3 scripts/register_agent.py --name "ChainStaff Finance Ops" --url https://your-public-url

Mainnet needs --network bsc-mainnet AND --i-understand-mainnet, and should use a Turnkey wallet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="ChainStaff Finance Ops")
    ap.add_argument("--description", default="AI finance operations employee with a spending-limited wallet. Built on BNB Chain.")
    ap.add_argument("--url", required=True, help="public URL of the employee (console or landing page)")
    ap.add_argument("--network", default="bsc-testnet", choices=["bsc-testnet", "bsc-mainnet"])
    ap.add_argument("--i-understand-mainnet", action="store_true")
    ap.add_argument("--out", default="data/agent_identity.json")
    args = ap.parse_args(argv)

    if args.network == "bsc-mainnet" and not args.i_understand_mainnet:
        print("主网注册需要老板明确同意，并加上 --i-understand-mainnet", file=sys.stderr)
        return 2

    from bnbagent import AgentEndpoint, ERC8004Agent, EVMWalletProvider  # pragma: no cover

    wallet = EVMWalletProvider(password=os.environ["WALLET_PASSWORD"], private_key=os.environ.get("PRIVATE_KEY"))
    sdk = ERC8004Agent(network=args.network, wallet_provider=wallet)
    uri = sdk.generate_agent_uri(name=args.name, description=args.description, endpoints=[AgentEndpoint.a2a(args.url)])
    result = sdk.register_agent(agent_uri=uri)
    out = {"network": args.network, "address": wallet.address, "agentId": result["agentId"], "tx": result["transactionHash"]}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
