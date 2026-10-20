#!/usr/bin/env bash
# Ubuntu 22.04 一键部署飞书机器人（长连接模式，无需公网 IP/域名/证书）
#
# 用法：
#   scp -r feishu-bot user@server:~/     # 或 git clone
#   cd ~/feishu-bot
#   chmod +x deploy/install.sh
#   ./deploy/install.sh
#
# 前置：同目录下必须已存在填好的 .env

set -euo pipefail

SERVICE_NAME="feishu-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUN_USER="$(id -un)"
VENV_PY="$PROJECT_DIR/.venv/bin/python"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 前置检查 ----------
[ -f "$PROJECT_DIR/bot.py" ] || die "没找到 bot.py，请在项目根目录执行"
[ -f "$PROJECT_DIR/.env" ] || die "缺少 .env。先把它传上来：scp .env user@server:$PROJECT_DIR/"

# .env 里有密钥，收紧权限
chmod 600 "$PROJECT_DIR/.env"

if ! command -v python3 >/dev/null 2>&1; then
    die "没有 python3，先执行：sudo apt update && sudo apt install -y python3 python3-venv"
fi

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "Python 版本：$PY_VER"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "需要 Python 3.9+，当前是 $PY_VER"

if ! python3 -c 'import venv' >/dev/null 2>&1; then
    die "缺少 venv 模块，执行：sudo apt install -y python3-venv"
fi

# ---------- 虚拟环境 ----------
if [ ! -x "$VENV_PY" ]; then
    say "创建虚拟环境"
    python3 -m venv "$PROJECT_DIR/.venv"
fi

say "安装依赖"
if [ -f "$PROJECT_DIR/requirements.lock" ]; then
    "$PROJECT_DIR/.venv/bin/pip" install -q --upgrade pip
    "$PROJECT_DIR/.venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.lock"
else
    "$PROJECT_DIR/.venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
fi

# ---------- 离线自检 ----------
say "运行离线自检（不连飞书、不调真实模型）"
if "$VENV_PY" "$PROJECT_DIR/offline_check.py" >/dev/null 2>&1; then
    say "自检通过"
else
    printf '\033[1;33m[警告]\033[0m 离线自检未通过，建议手动执行排查：\n'
    printf '    %s %s/offline_check.py\n' "$VENV_PY" "$PROJECT_DIR"
fi

# ---------- systemd ----------
say "写入 systemd 服务（$SERVICE_NAME）"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=Feishu LLM Bot (long connection)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=$VENV_PY $PROJECT_DIR/bot.py
Restart=always
RestartSec=5
# 反复崩溃时不要无限重启，1 分钟内超过 10 次就停下
StartLimitIntervalSec=60
StartLimitBurst=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

sleep 2
say "当前状态"
sudo systemctl --no-pager --lines=15 status "$SERVICE_NAME" || true

cat <<EOF

$(printf '\033[1;32m完成\033[0m')

查看实时日志：  journalctl -u $SERVICE_NAME -f
重启：          sudo systemctl restart $SERVICE_NAME
停止：          sudo systemctl stop $SERVICE_NAME
停止并禁用自启：sudo systemctl disable --now $SERVICE_NAME

⚠️  如果本机/别的电脑上还开着 bot.py，请立刻停掉。
    同一个应用开两条长连接，飞书会把事件随机分给其中一条，导致时好时坏。
EOF