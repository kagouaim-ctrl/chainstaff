import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("public_demo", ROOT / "scripts" / "public_demo.py")
public_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public_demo)


def test_demo_starts_with_only_a_bot_token():
    assert public_demo.check_env({"TELEGRAM_BOT_TOKEN": "123:abc"}) == []


def test_demo_needs_a_bot_token():
    assert any("TELEGRAM_BOT_TOKEN" in p for p in public_demo.check_env({}))


def test_demo_refuses_anything_that_could_move_money():
    for key in public_demo.LIVE_SETTINGS + ("TURNKEY_API_PRIVATE_KEY",):
        problems = public_demo.check_env({"TELEGRAM_BOT_TOKEN": "123:abc", key: "x"})
        assert any(key in p for p in problems), key


def test_demo_console_token_must_be_long_enough():
    assert public_demo.check_env({"TELEGRAM_BOT_TOKEN": "t", "CHAINSTAFF_CONSOLE_TOKEN": "short"})
    assert not public_demo.check_env({"TELEGRAM_BOT_TOKEN": "t", "CHAINSTAFF_CONSOLE_TOKEN": "x" * 16})


def test_demo_defaults_do_not_override_the_host():
    env = public_demo.demo_env({"CHAINSTAFF_DB": "/data/x.db"})
    assert env["CHAINSTAFF_DB"] == "/data/x.db"
    assert env["CHAINSTAFF_POLICY"] == "config/policy.demo.json"


def test_demo_policy_has_no_owners_and_loads():
    from agent.config import PolicyFile

    assert json.loads((ROOT / "config" / "policy.demo.json").read_text())["owners"] == []
    policy, owners = PolicyFile(ROOT / "config" / "policy.demo.json").load()
    assert owners == frozenset() and policy.whitelist
