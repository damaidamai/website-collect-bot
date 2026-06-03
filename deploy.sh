#!/bin/bash
set -e

# 确保在脚本所在根目录下执行
cd "$(dirname "$0")"

echo "========== [1/5] 运行本地单元测试 =========="
uv run pytest

echo "========== [2/5] 检查并推送代码至 GitHub =========="
if ! git diff-index --quiet HEAD --; then
    echo "检测到本地有未提交的代码，建议先手动 commit 并 push！"
    exit 1
fi
echo "确认代码已全部提交，正在推送至 GitHub 远端仓库..."
git push origin main

echo "========== [3/5] SSH 连接 hk2H4G 并拉取代码 =========="
ssh hk2H4G "cd /root/projects/website-collect-bot && git pull"

echo "========== [4/5] 远程重启 website-collect-bot 服务 =========="
ssh hk2H4G "systemctl restart website-collect-bot.service"

echo "========== [5/5] 检查远程服务运行状态 =========="
ssh hk2H4G "systemctl status website-collect-bot.service --no-pager"

echo "🎉 一键部署已成功完成！"
