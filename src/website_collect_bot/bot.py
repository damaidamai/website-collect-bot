from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from website_collect_bot.config import Settings
from website_collect_bot.deepseek import DeepSeekClient
from website_collect_bot.extract import extract_domains, normalize_domain
from website_collect_bot.models import SiteStatus, normalize_status
from website_collect_bot.storage import Storage

logger = logging.getLogger(__name__)


HELP_TEXT = """可用命令：
/list 查看全部网站
/todo 查看待处理网站
/done 查看已处理网站
/site <domain> 查看网站详情
/status <domain> <状态> 更新状态

也可以直接说：
待处理列表
全部网站
查 example.com
把 example.com 标为已处理
"""


@dataclass(frozen=True)
class NaturalLanguageIntent:
    name: str
    domain: str | None = None
    status: str | None = None


class WebsiteCollectBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.database_path)
        self.deepseek = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )

    async def initialize(self) -> None:
        await self.storage.init()

    def build_application(self) -> Application:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN，请在 .env 中配置。")

        app = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .post_init(self.post_init)
            .build()
        )
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("list", self.list_command))
        app.add_handler(CommandHandler("todo", self.todo_command))
        app.add_handler(CommandHandler("done", self.done_command))
        app.add_handler(CommandHandler("site", self.site_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        return app

    async def post_init(self, app: Application) -> None:
        await self.initialize()

    def is_allowed_chat(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        allowed_id = self.settings.telegram_allowed_chat_id
        if allowed_id is None:
            logger.info("收到 chat_id=%s。配置 TELEGRAM_ALLOWED_CHAT_ID 后可限制单群。", chat.id)
            return True
        return chat.id == allowed_id

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_allowed_chat(update):
            return
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not message.text:
            return

        intent = parse_natural_language_intent(message.text)
        if intent is not None and await self.handle_natural_language_intent(update, intent):
            return

        domains = extract_domains(message.text)
        if not domains:
            return

        sender = update.effective_user.full_name if update.effective_user else None
        message_id = await self.storage.record_message(
            telegram_message_id=message.message_id,
            chat_id=chat.id,
            sender_name=sender,
            message_text=message.text,
        )

        changed: list[str] = []
        for domain in domains:
            existing = await self.storage.get_site(domain)
            recent = await self.storage.recent_site_messages(existing.id) if existing else []
            analysis = await self.deepseek.analyze_site_message(
                domain=domain,
                message_text=message.text,
                existing_summary=existing.summary if existing else "",
                recent_messages=recent,
            )
            site = await self.storage.upsert_site(
                domain=domain,
                canonical_url=analysis.canonical_url,
                title=analysis.title,
                summary=analysis.summary,
                notes=analysis.notes,
                status=analysis.status,
            )
            await self.storage.link_message_to_site(message_id, site.id)
            await self.storage.add_event(site.id, "message_analysis", analysis.notes or analysis.summary)
            action = "已更新" if existing else "已记录"
            changed.append(f"{action}：{site.domain}｜{site.status}")

        await message.reply_text("\n".join(changed[:3]))

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.is_allowed_chat(update) and update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply_site_list(update, None, "全部网站")

    async def todo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply_site_list(update, SiteStatus.TODO.value, "待处理网站")

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply_site_list(update, SiteStatus.DONE.value, "已处理网站")

    async def handle_natural_language_intent(
        self,
        update: Update,
        intent: NaturalLanguageIntent,
    ) -> bool:
        message = update.effective_message
        if message is None:
            return False
        if intent.name == "help":
            await message.reply_text(HELP_TEXT)
            return True
        if intent.name == "list":
            title = list_title(intent.status)
            await self.reply_site_list(update, intent.status, title)
            return True
        if intent.name == "site" and intent.domain:
            await self.reply_site_detail(update, intent.domain)
            return True
        if intent.name == "status" and intent.domain and intent.status:
            await self.update_site_status(update, intent.domain, intent.status, "自然语言更新")
            return True
        return False

    async def reply_site_list(self, update: Update, status: str | None, title: str) -> None:
        if not self.is_allowed_chat(update) or update.effective_message is None:
            return
        sites = await self.storage.list_sites(status=status, limit=20)
        if not sites:
            await update.effective_message.reply_text(f"{title}：暂无")
            return
        lines = [f"{title}："]
        for index, site in enumerate(sites, start=1):
            summary = f" - {site.summary[:40]}" if site.summary else ""
            lines.append(f"{index}. {site.domain}｜{site.status}{summary}")
        await update.effective_message.reply_text("\n".join(lines))

    async def site_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_allowed_chat(update) or update.effective_message is None:
            return
        if not context.args:
            await update.effective_message.reply_text("用法：/site <domain>")
            return

        domain = normalize_domain(context.args[0])
        await self.reply_site_detail(update, domain)

    async def reply_site_detail(self, update: Update, domain: str) -> None:
        if not self.is_allowed_chat(update) or update.effective_message is None:
            return
        site = await self.storage.get_site(domain)
        if site is None:
            await update.effective_message.reply_text(f"未找到：{domain}")
            return

        lines = [
            f"{site.domain}",
            f"状态：{site.status}",
            f"URL：{site.canonical_url or '-'}",
            f"摘要：{site.summary or '-'}",
        ]
        if site.notes:
            lines.append(f"备注：{site.notes[:500]}")
        await update.effective_message.reply_text("\n".join(lines))

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_allowed_chat(update) or update.effective_message is None:
            return
        if len(context.args) < 2:
            await update.effective_message.reply_text("用法：/status <domain> <状态>")
            return

        domain = normalize_domain(context.args[0])
        status = normalize_status(" ".join(context.args[1:]))
        if status is None:
            await update.effective_message.reply_text(
                "状态必须是：待处理 / 处理中 / 已处理 / 搁置 / 无需处理"
            )
            return

        await self.update_site_status(update, domain, status, "命令更新")

    async def update_site_status(
        self,
        update: Update,
        domain: str,
        status: str,
        reason: str,
    ) -> None:
        if not self.is_allowed_chat(update) or update.effective_message is None:
            return
        site = await self.storage.set_status(domain, status, reason=reason)
        if site is None:
            await update.effective_message.reply_text(f"未找到：{domain}")
            return
        await update.effective_message.reply_text(f"已更新：{site.domain}｜{site.status}")


def parse_natural_language_intent(text: str) -> NaturalLanguageIntent | None:
    cleaned = clean_natural_language_text(text)
    if not cleaned:
        return None

    if any(token in cleaned for token in ("帮助", "怎么用", "使用说明")):
        return NaturalLanguageIntent("help")

    status = find_status_in_text(cleaned)
    domains = extract_domains(cleaned)
    if domains and status and re.search(r"(状态|改成|改为|设为|设置为|标为|标记为|更新为|变成)", cleaned):
        return NaturalLanguageIntent("status", domain=domains[0], status=status)

    if domains and re.search(r"(查|查看|看看|详情|信息|摘要|状态)", cleaned):
        return NaturalLanguageIntent("site", domain=domains[0])

    if is_list_request(cleaned):
        return NaturalLanguageIntent("list", status=status)

    return None


def clean_natural_language_text(text: str) -> str:
    text = re.sub(r"@\w+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def find_status_in_text(text: str) -> str | None:
    for token in (
        SiteStatus.NO_ACTION.value,
        SiteStatus.IN_PROGRESS.value,
        SiteStatus.DONE.value,
        SiteStatus.PAUSED.value,
        SiteStatus.TODO.value,
        "不用处理",
        "已完成",
        "完成",
        "处理完",
        "待办",
        "暂停",
        "doing",
        "done",
        "todo",
    ):
        if token in text:
            normalized = normalize_status(token)
            if normalized:
                return normalized
    return None


def is_list_request(text: str) -> bool:
    if re.search(r"(列表|清单|有哪些|有多少|列出|查看|看看)", text):
        return any(token in text for token in ("网站", "待处理", "待办", "处理中", "已处理", "搁置", "无需处理", "全部", "所有"))
    return text in {"待处理", "待办", "处理中", "已处理", "搁置", "无需处理", "全部网站", "所有网站"}


def list_title(status: str | None) -> str:
    if status is None:
        return "全部网站"
    return f"{status}网站"
