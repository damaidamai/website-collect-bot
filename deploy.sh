#!/bin/bash
set -e

# 确保在脚本所在根目录下执行
cd "$(dirname "$0")"

SERVER="JP2C4G"
REMOTE_DIR="/home/damai/projects/website-collect-bot"
REPO_URL="git@github.com:damaidamai/website-collect-bot.git"
SERVICE_NAME="website-collect-bot.service"

echo "========== [1/5] 运行本地单元测试 =========="
uv run pytest

echo "========== [2/5] 检查并推送代码至 GitHub =========="
if ! git diff-index --quiet HEAD --; then
    echo "检测到本地有未提交的代码，建议先手动 commit 并 push！"
    exit 1
fi
echo "确认代码已全部提交，正在推送至 GitHub 远端仓库..."
git push origin main

echo "========== [3/5] SSH 连接 ${SERVER} 并同步代码与配置 =========="
ssh "${SERVER}" "
set -e
mkdir -p \"$(dirname "${REMOTE_DIR}")\"
if [ ! -d \"${REMOTE_DIR}/.git\" ]; then
    git clone \"${REPO_URL}\" \"${REMOTE_DIR}\"
else
    cd \"${REMOTE_DIR}\" && git pull --ff-only
fi
if ! command -v uv >/dev/null 2>&1 && [ ! -x /home/damai/.local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
"
rsync -az .env "${SERVER}:${REMOTE_DIR}/.env"
rsync -az data/ "${SERVER}:${REMOTE_DIR}/data/"
ssh "${SERVER}" "
set -e
cd \"${REMOTE_DIR}\"
chmod 600 .env
mkdir -p data
if command -v uv >/dev/null 2>&1; then
    UV_BIN=\$(command -v uv)
else
    UV_BIN=\"/home/damai/.local/bin/uv\"
fi
\${UV_BIN} sync --frozen
cat > /tmp/${SERVICE_NAME} <<EOF
[Unit]
Description=Website Collect Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
ExecStart=${REMOTE_DIR}/.venv/bin/website-collect-bot
Restart=always
RestartSec=5
User=damai
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/${SERVICE_NAME} /etc/systemd/system/${SERVICE_NAME}
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
"

echo "========== [4/5] 远程重启 website-collect-bot 服务 =========="
ssh "${SERVER}" "sudo systemctl restart ${SERVICE_NAME}"

echo "========== [5/5] 检查远程服务运行状态 =========="
ssh "${SERVER}" "sudo systemctl status ${SERVICE_NAME} --no-pager"

echo "🎉 一键部署已成功完成！"
