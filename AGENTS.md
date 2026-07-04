使用中文与用户沟通。

涉及 Python 代码优先使用 uv 管理依赖和运行测试。
涉及前端代码优先使用 pnpm，而不是 npm。

本项目的线上部署目标是 `ssh JP2C4G`，不要默认部署到 `hk2H4G` 或 `openclaw`。

部署信息：
- 服务器 SSH 别名：`JP2C4G`
- 服务器 IP：`207.56.229.121`
- SSH 用户：`damai`
- SSH 端口：`2221`
- 服务器项目目录：`/home/damai/projects/website-collect-bot`
- systemd 服务：`website-collect-bot.service`
- Web 面板服务：`website-collect-web.service`
- Web 面板端口：`8080`
- SQLite 每日备份 timer：`website-collect-bot-sqlite-backup.timer`
- 推荐部署命令：`./deploy.sh`

手动部署流程：
1. 本地运行 `uv run pytest`
2. 提交并推送到 `origin main`
3. 远程执行 `ssh JP2C4G "cd /home/damai/projects/website-collect-bot && git pull"`
4. 远程执行 `ssh JP2C4G "sudo systemctl restart website-collect-bot.service"`
5. 远程执行 `ssh JP2C4G "sudo systemctl restart website-collect-web.service"`
6. 远程执行 `ssh JP2C4G "sudo systemctl status website-collect-bot.service --no-pager"`
7. 远程执行 `ssh JP2C4G "sudo systemctl status website-collect-web.service --no-pager"`

SQLite 数据库 `data/sites.sqlite3` 已纳入 Git 管理。VPS 上通过
`website-collect-bot-sqlite-backup.timer` 每日自动提交并推送最新 SQLite。
