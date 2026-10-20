#!/usr/bin/env bash
# 前台试跑（调试用）。正式常驻请用 deploy/install.sh 注册 systemd 服务。
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "缺少 .env：先执行 cp .env.example .env，再填写三项" >&2
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "缺少 .venv：先执行 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

exec .venv/bin/python bot.py