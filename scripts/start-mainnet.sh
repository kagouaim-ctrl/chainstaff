#!/usr/bin/env bash
# One-command mainnet start: ./scripts/start-mainnet.sh
# Reads non-secret settings from config/mainnet.env, asks for the two secrets
# without echoing them, and never writes them to disk or shell history.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config/mainnet.env ]; then
  echo "缺少 config/mainnet.env：先运行 cp config/mainnet.env.example config/mainnet.env 并填好" >&2
  exit 1
fi
[ -f .venv/bin/activate ] && source .venv/bin/activate
set -a; source config/mainnet.env; set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  read -rsp "粘贴 Telegram 机器人 token，回车（不会显示）：" TELEGRAM_BOT_TOKEN; echo
  export TELEGRAM_BOT_TOKEN
fi
if [ -z "${TURNKEY_API_PRIVATE_KEY:-}" ]; then
  read -rsp "粘贴 Turnkey API 私钥，回车（不会显示）：" TURNKEY_API_PRIVATE_KEY; echo
  export TURNKEY_API_PRIVATE_KEY
fi

exec python3 -m bot.main
