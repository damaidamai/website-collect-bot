# Website Collect Bot

Telegram 群机器人，用于从群聊中收集网站信息，维护每个网站的状态、摘要和讨论记录。

## 功能

- 监听一个 Telegram 群的新消息
- 抽取 URL / 域名
- 通过 DeepSeek 分析消息，生成网站摘要并识别状态更新
- 使用 SQLite 持久化网站、消息、事件和状态历史
- 支持群内命令查询和更新状态

## 配置

```bash
cp .env.example .env
```

填写：

- `TELEGRAM_BOT_TOKEN`: Telegram Bot Token
- `TELEGRAM_ALLOWED_CHAT_ID`: 允许服务的群 ID，留空时第一次启动会打印收到的 chat id
- `DEEPSEEK_API_KEY`: DeepSeek API Key
- `DEEPSEEK_MODEL`: DeepSeek 模型 ID，默认 `deepseek-v4-flash`；需要更强能力时可改为 `deepseek-v4-pro`

## 启动

```bash
uv sync
uv run website-collect-bot
```

## 命令

- `/list`：查看全部网站
- `/todo`：查看待处理网站
- `/done`：查看已处理网站
- `/site <domain>`：查看网站详情
- `/status <domain> <状态>`：更新状态
- `/help`：查看帮助

支持状态：

- `待处理`
- `处理中`
- `已处理`
- `搁置`
- `无需处理`

## 示例

```text
https://example.com 这个站需要看一下，疑似有登录问题。
```

机器人会简要回复：

```text
已记录：example.com｜待处理
```

后续消息：

```text
example.com 已经处理好了，是配置问题。
```

机器人会更新状态和摘要：

```text
已更新：example.com｜已处理
```
