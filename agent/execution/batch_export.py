"""Export a payout batch for the Safe owners to sign. Nothing here signs or sends.

Two formats the Safe{Wallet} web app reads:
- Transaction Builder JSON: one transfer per line, the Safe bundles them into one MultiSend.
- CSV Airdrop app CSV: token_type,token_address,receiver,amount,id
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from eth_utils import to_checksum_address

from .erc20 import encode_transfer
from .tokens import TokenInfo, to_base_units


def tx_builder_json(chain_id: int, safe: str | None, token: TokenInfo, items: Iterable[tuple[str, Decimal]],
                    name: str, description: str = "", created_at: datetime | None = None) -> str:
    txs = []
    for address, amount in items:
        units = to_base_units(amount, token.decimals)
        if token.is_native:
            txs.append({"to": to_checksum_address(address), "value": str(units), "data": "0x",
                        "contractMethod": None, "contractInputsValues": None})
        else:
            txs.append({"to": to_checksum_address(token.address), "value": "0",
                        "data": "0x" + encode_transfer(address, units).hex(),
                        "contractMethod": None, "contractInputsValues": None})
    at = created_at or datetime.now(timezone.utc)
    doc = {
        "version": "1.0",
        "chainId": str(chain_id),
        "createdAt": int(at.timestamp() * 1000),
        "meta": {
            "name": name[:80],
            "description": description[:400],
            "txBuilderVersion": "1.16.5",
            "createdFromSafeAddress": to_checksum_address(safe) if safe else "",
            "createdFromOwnerAddress": "",
        },
        "transactions": txs,
    }
    return json.dumps(doc, indent=2)


def csv_airdrop(token: TokenInfo, items: Iterable[tuple[str, Decimal]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["token_type", "token_address", "receiver", "amount", "id"])
    for address, amount in items:
        to_base_units(amount, token.decimals)  # refuse amounts the token can't represent
        w.writerow(["native" if token.is_native else "erc20", "" if token.is_native else to_checksum_address(token.address),
                    to_checksum_address(address), f"{amount.normalize():f}", ""])
    return buf.getvalue()
