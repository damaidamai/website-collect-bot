# Website Collect Bot 部署指南

本项目已部署在 `hk2H4G` 服务器上，通过 Systemd 进行服务托管。

---

## 1. 托管环境信息

- **服务器 SSH 别名**：`hk2H4G` (配置在本地 `~/.ssh/config` 中)
  - IP 地址：`156.247.41.174`
  - SSH 端口：`22`
  - SSH 用户：`root`
- **服务器项目工作目录**：`/root/projects/website-collect-bot`
- **运行环境**：`.venv` (Python 虚拟环境，Python 3.12)
- **托管服务名称**：`website-collect-bot.service`

---

## 2. 自动化一键部署（推荐）

我们在项目根目录下编写了 [deploy.sh](file:///Users/linglin/projects/website-collect-bot/deploy.sh) 自动化脚本。在本地开发终端中，直接运行以下命令即可完成自动测试、推送、远程拉取和重启：

```bash
./deploy.sh
```

**脚本内部流程**：
1. 本地自动执行 `uv run pytest` 跑通单元测试。
2. 检查本地是否有未提交的改动并要求先 commit。
3. 执行 `git push origin main` 将修改推送至 GitHub 远端仓库。
4. SSH 连接服务器 `hk2H4G` 执行 `git pull` 拉取最新代码。
5. 在服务器上运行 `systemctl restart website-collect-bot.service` 重启机器人。
6. 显示机器人服务的最新状态。

---

## 3. 手动部署步骤

如果需要逐步排查或手动执行部署，可遵循以下流程：

### 第一步：在本地提交并推送代码
```bash
git add .
git commit -m "Your commit message"
git push origin main
```

### 第二步：SSH 登录服务器并更新代码
```bash
# 登录服务器
ssh hk2H4G

# 进入项目工作目录并拉取最新代码
cd /root/projects/website-collect-bot
git pull
```

### 第三步：重启 Telegram 机器人服务
```bash
systemctl restart website-collect-bot.service
```

### 第四步：检查服务运行状态
```bash
# 检查运行状态（状态应为 active (running)）
systemctl status website-collect-bot.service

# 查看实时运行日志
journalctl -u website-collect-bot.service -f -n 50
```
