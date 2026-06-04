from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from website_collect_bot.extract import canonical_site_key, extract_domains, normalize_domain
from website_collect_bot.models import SiteStatus, normalize_status


@dataclass(frozen=True)
class NaturalLanguageIntent:
    name: str
    domain: str | None = None
    status: str | None = None
    notes: str | None = None


def parse_natural_language_intent(text: str) -> NaturalLanguageIntent | None:
    cleaned = clean_natural_language_text(text)
    if not cleaned:
        return None

    if any(token in cleaned for token in ("帮助", "怎么用", "使用说明")):
        return NaturalLanguageIntent("help")

    status = find_status_in_text(cleaned)
    domains = extract_domains(cleaned)
    if domains and status and re.search(r"(状态|改成|改为|设为|设置为|标为|标记为|更新为|变成)", cleaned):
        return NaturalLanguageIntent("status", domain=canonical_site_key(domains[0]), status=status)

    if domains and re.search(r"(查|查看|看看|详情|信息|摘要|状态)", cleaned):
        return NaturalLanguageIntent("site", domain=canonical_site_key(domains[0]))

    if is_list_request(cleaned):
        return NaturalLanguageIntent("list", status=status)

    return None


def coerce_ai_intent(data: dict[str, Any]) -> NaturalLanguageIntent | None:
    intent = str(data.get("intent") or "").strip().lower()
    if intent in {"none", "null", ""}:
        return None
    if intent not in {"help", "list", "site", "status"}:
        return None

    confidence = data.get("confidence", 0.0)
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.0
    if confidence_float < 0.65:
        return None

    status = normalize_status(str(data.get("status") or "")) if data.get("status") else None
    raw_domain = str(data.get("domain") or "").strip()
    domain = canonical_site_key(normalize_domain(raw_domain)) if raw_domain else None

    if intent == "status" and (domain is None or status is None):
        return None
    if intent == "site" and domain is None:
        return None

    raw_notes = data.get("notes") or data.get("reason")
    notes = str(raw_notes).strip() if raw_notes else None

    return NaturalLanguageIntent(intent, domain=domain, status=status, notes=notes)


def should_use_ai_intent(text: str, bot_username: str | None = None) -> bool:
    cleaned = clean_natural_language_text(text)
    if not cleaned or len(cleaned) > 500:
        return False

    lowered = text.lower()
    if bot_username and f"@{bot_username.lower()}" in lowered:
        return True
    if re.search(r"@\w+_bot\b", lowered):
        return True

    return bool(
        re.search(
            r"(待办|待处理|没处理|处理中|已处理|完成|搁置|不用处理|无需处理|列表|清单|"
            r"有哪些|有多少|查一下|看看|详情|摘要|状态|改成|改为|标为|设置为|帮助|怎么用|放弃|爆破不了)",
            cleaned,
        )
    )


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
        "放弃",
        "爆破不了",
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
        return any(
            token in text
            for token in ("网站", "待处理", "待办", "处理中", "已处理", "搁置", "无需处理", "全部", "所有")
        )
    return text in {"待处理", "待办", "处理中", "已处理", "搁置", "无需处理", "全部网站", "所有网站"}


def list_title(status: str | None) -> str:
    if status is None:
        return "全部网站"
    return f"{status}网站"
