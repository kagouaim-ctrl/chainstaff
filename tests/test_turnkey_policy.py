import json

import pytest

from agent.execution.erc20 import encode_transfer
from agent.execution.turnkey_policy import condition, policy_json

USDT = "0x55d398326f99059fF775485246999027B3197955"
ALICE = "0xA11ce00000000000000000000000000000007e2D"
BOB = "0xB0b5000000000000000000000000000000041a90"


def test_condition_slices_match_real_transfer_calldata():
    data = "0x" + encode_transfer(ALICE, 5 * 10**18).hex()
    assert data[0:10] == "0xa9059cbb"
    assert data[34:74] == ALICE.lower()[2:]
    c = condition(56, USDT, [BOB, ALICE, ALICE.lower()])
    assert c.startswith("eth.tx.chain_id == 56 && eth.tx.to == '0x55d398326f99059ff775485246999027b3197955' && eth.tx.value == 0")
    assert c.count("eth.tx.data[34..74]") == 2  # deduplicated
    assert f"'{ALICE.lower()[2:]}'" in c and f"'{BOB.lower()[2:]}'" in c


def test_empty_whitelist_refused():
    with pytest.raises(ValueError):
        condition(56, USDT, [])


def test_policy_json_and_script(tmp_path, capsys):
    p = json.loads(policy_json(56, USDT, [ALICE], "user-123"))
    assert p["effect"] == "EFFECT_ALLOW" and "user-123" in p["consensus"]

    from scripts.turnkey_policy import main

    assert main(["--user-id", "u1", "--policy", "config/policy.example.json", "--chain", "config/chain.mainnet.example.json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "eth.tx.chain_id == 56" in out["condition"]
    assert main(["--user-id", "u1", "--policy", "config/policy.example.json", "--chain", "config/chain.example.json"]) == 2
