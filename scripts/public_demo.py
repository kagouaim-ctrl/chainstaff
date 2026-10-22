"""Public Telegram demo: the bot, plus the console when a token is set, in one process.

Anyone can join the demo group, so this entry point only ever runs in test mode: it refuses
to start when a chain, a wallet or live mode is configured. Payments are dry runs and nothing
touches a chain.

    TELEGRAM_BOT_TOKEN        from @BotFather; set it in the host's secret settings, never in git
    CHAINSTAFF_CONSOLE_TOKEN  optional: also serve the console on $PORT (at least 16 characters)
    CHAINSTAFF_POLICY         defaults to config/policy.demo.json (sample payees, no owners)
    CHAINSTAFF_DB             defaults to data/demo.db

    python3 scripts/public_demo.py
"""

from __future__ import annotations

import asyncio
import os

# Anything that could reach real money. The demo must start without all of them.
LIVE_SETTINGS = ("CHAINSTAFF_LIVE", "CHAINSTAFF_CHAIN", "CHAINSTAFF_WALLET", "CHAINSTAFF_WALLET_PASSWORD", "CHAINSTAFF_ALLOW_MAINNET")


def check_env(env: dict[str, str]) -> list[str]:
    """Reasons the public demo must not start; empty when it is safe to run."""
    problems = [f"公开体验只能用测试模式，请删除环境变量 {k}" for k in LIVE_SETTINGS if env.get(k)]
    problems += [f"公开体验只能用测试模式，请删除环境变量 {k}" for k in sorted(env) if k.startswith("TURNKEY_")]
    if not env.get("TELEGRAM_BOT_TOKEN"):
        problems.append("缺少 TELEGRAM_BOT_TOKEN：在部署平台的环境变量里填 BotFather 给的 token")
    token = env.get("CHAINSTAFF_CONSOLE_TOKEN", "")
    if token and len(token) < 16:
        problems.append("CHAINSTAFF_CONSOLE_TOKEN 至少 16 个字符")
    return problems


def demo_env(env: dict[str, str]) -> dict[str, str]:
    """Fill in demo defaults without overriding anything the host set."""
    out = dict(env)
    out.setdefault("CHAINSTAFF_POLICY", "config/policy.demo.json")
    out.setdefault("CHAINSTAFF_DB", "data/demo.db")
    return out


async def run() -> None:  # pragma: no cover - needs a live token
    import uvicorn

    from bot.main import main as bot_main

    jobs = [bot_main()]
    if os.environ.get("CHAINSTAFF_CONSOLE_TOKEN"):
        from backend.app import _default_app

        config = uvicorn.Config(_default_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
        jobs.append(uvicorn.Server(config).serve())
    await asyncio.gather(*jobs)


if __name__ == "__main__":  # pragma: no cover
    problems = check_env(dict(os.environ))
    if problems:
        raise SystemExit("\n".join(problems))
    os.environ.update(demo_env(dict(os.environ)))
    asyncio.run(run())
