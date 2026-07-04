from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

from website_collect_bot.config import get_settings
from website_collect_bot.deepseek import DeepSeekClient
from website_collect_bot.extract import canonical_site_key, extract_domains
from website_collect_bot.storage import Storage


SKIP_DOMAINS = {"t.me", "showdoc.com.cn"}
BOT_NAMES = {"cute73"}


@dataclass
class ExportedMessage:
    id: int
    sender: str = ""
    text: str = ""
    date_title: str = ""
    reply_to: int | None = None


class TelegramHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[ExportedMessage] = []
        self.current: ExportedMessage | None = None
        self.message_depth = 0
        self.capture: str | None = None
        self.buffer: list[str] = []
        self.reply_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        css = attr.get("class", "")
        classes = set(css.split())

        if tag == "div" and "message" in classes and attr.get("id", "").startswith("message"):
            raw_id = attr["id"].removeprefix("message").replace("-", "")
            self.current = ExportedMessage(id=int(raw_id))
            self.message_depth = 1
            return

        if self.current is None:
            return

        if tag == "div":
            self.message_depth += 1

        if tag == "div" and {"pull_right", "date", "details"}.issubset(classes):
            self.current.date_title = attr.get("title", "")
        elif tag == "div" and "reply_to" in classes:
            self.reply_depth = self.message_depth
        elif self.reply_depth and tag == "a":
            href = attr.get("href", "")
            if href.startswith("#go_to_message"):
                self.current.reply_to = int(href.removeprefix("#go_to_message"))
        elif tag == "div" and "from_name" in classes:
            self.capture = "sender"
            self.buffer = []
        elif tag == "div" and "text" in classes:
            self.capture = "text"
            self.buffer = []
        elif tag == "br" and self.capture:
            self.buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.current is None or tag != "div":
            return

        if self.capture in {"sender", "text"}:
            value = clean_text("".join(self.buffer))
            if self.capture == "sender":
                self.current.sender = value
            else:
                self.current.text = value
            self.capture = None
            self.buffer = []

        if self.reply_depth == self.message_depth:
            self.reply_depth = 0

        self.message_depth -= 1
        if self.message_depth == 0:
            if self.current.text:
                self.messages.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def parse_export(path: Path) -> list[ExportedMessage]:
    parser = TelegramHtmlParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.messages


def is_bot_message(message: ExportedMessage) -> bool:
    sender = message.sender.strip()
    if sender in BOT_NAMES:
        return True
    return message.text.startswith(("已记录：", "已更新：", "已删除：", "待处理网站：", "全部网站："))


def is_query_only(text: str) -> bool:
    cleaned = text.strip()
    return bool(
        re.search(r"(^|\\s)(详情|待处理列表|全部网站|所有网站)$", cleaned)
        or re.search(r"@\\w+_bot\\s+(待处理列表|全部网站|所有网站)", cleaned)
    )


def deleted_domains(text: str) -> list[str]:
    if "删除" not in text:
        return []
    return business_domains(text)


def business_domains(text: str) -> list[str]:
    return [domain for domain in extract_domains(text) if domain not in SKIP_DOMAINS]


def message_created_at(message: ExportedMessage) -> str:
    if not message.date_title:
        return datetime.now().astimezone().isoformat()
    raw = message.date_title.replace(" UTC+08:00", " +0800")
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M:%S %z").isoformat()
    except ValueError:
        return datetime.now().astimezone().isoformat()


async def import_messages(export_path: Path, reset: bool) -> None:
    load_dotenv()
    settings = get_settings()
    db_path = settings.database_path
    if reset and db_path.exists():
        backup = db_path.with_suffix(f".sqlite3.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(db_path, backup)
        db_path.unlink()
        print(f"Backed up existing database to {backup}")

    storage = Storage(db_path)
    await storage.init()
    deepseek = DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )

    messages = parse_export(export_path)
    by_id = {message.id: message for message in messages}
    imported = 0
    deleted = 0
    skipped = 0

    for message in messages:
        if is_bot_message(message):
            skipped += 1
            continue

        reply_context = by_id[message.reply_to].text if message.reply_to in by_id else ""
        context_text = f"{message.text}\n{reply_context}" if reply_context else message.text

        delete_targets = deleted_domains(message.text)
        if delete_targets:
            for domain in delete_targets:
                await delete_site(storage.database_path, canonical_site_key(domain))
                deleted += 1
            continue

        if is_query_only(message.text):
            skipped += 1
            continue

        domains = business_domains(context_text)
        if not domains:
            skipped += 1
            continue

        message_id = await storage.record_message(
            telegram_message_id=message.id,
            chat_id=0,
            sender_name=message.sender or None,
            message_text=message.text,
        )
        await set_message_created_at(storage.database_path, message_id, message_created_at(message))

        grouped: dict[str, list[str]] = {}
        for domain in domains:
            site_key = canonical_site_key(domain)
            grouped.setdefault(site_key, [])
            if domain not in grouped[site_key]:
                grouped[site_key].append(domain)

        for site_key, related_domains in grouped.items():
            existing = await storage.get_site(site_key)
            recent = await storage.recent_site_messages(existing.id) if existing else []
            analysis = await deepseek.analyze_site_message(
                site_key=site_key,
                message_text=context_text,
                related_domains=related_domains,
                existing_summary=existing.summary if existing else "",
                existing_status=existing.status if existing else "",
                existing_notes=existing.notes if existing else "",
                recent_messages=recent,
            )
            site = await storage.upsert_site(
                domain=site_key,
                canonical_url=analysis.canonical_url,
                title=analysis.title,
                summary=analysis.summary,
                notes=analysis.notes,
                status=analysis.status,
            )
            await storage.link_message_to_site(message_id, site.id)
            await storage.add_event(site.id, "telegram_html_import", analysis.notes or analysis.summary)
            imported += 1
            print(f"[{imported}] {site.domain} | {site.status}")

    print(f"Imported site updates: {imported}")
    print(f"Deleted site records: {deleted}")
    print(f"Skipped messages: {skipped}")


async def delete_site(db_path: Path, domain: str) -> None:
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT id FROM sites WHERE domain = ?", (domain,)).fetchone()
        if row is None:
            return
        site_id = int(row[0])
        db.execute("DELETE FROM site_messages WHERE site_id = ?", (site_id,))
        db.execute("DELETE FROM site_events WHERE site_id = ?", (site_id,))
        db.execute("DELETE FROM status_history WHERE site_id = ?", (site_id,))
        db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        db.commit()


async def set_message_created_at(db_path: Path, message_id: int, created_at: str) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (created_at, message_id))
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_html", type=Path)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    asyncio.run(import_messages(args.export_html, reset=args.reset))


if __name__ == "__main__":
    main()
