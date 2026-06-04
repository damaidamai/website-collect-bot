使用中文与用户沟通。

涉及 Python 代码优先使用 uv 管理依赖和运行测试。
涉及前端代码优先使用 pnpm，而不是 npm。

本项目的线上部署目标是 `ssh hk2H4G`，不要默认部署到 `openclaw`。

部署信息：
- 服务器 SSH 别名：`hk2H4G`
- 服务器项目目录：`/root/projects/website-collect-bot`
- systemd 服务：`website-collect-bot.service`
- 推荐部署命令：`./deploy.sh`

手动部署流程：
1. 本地运行 `uv run pytest`
2. 提交并推送到 `origin main`
3. 远程执行 `ssh hk2H4G "cd /root/projects/website-collect-bot && git pull"`
4. 远程执行 `ssh hk2H4G "systemctl restart website-collect-bot.service"`
5. 远程执行 `ssh hk2H4G "systemctl status website-collect-bot.service --no-pager"`
