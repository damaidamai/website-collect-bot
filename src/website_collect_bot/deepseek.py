from __future__ import annotations

import json
from typing import Any

import httpx

from website_collect_bot.extract import extract_first_url_for_domains
from website_collect_bot.models import AnalysisResult, SiteStatus, normalize_status


SYSTEM_PROMPT = """你是一个 Telegram 群聊信息整理助手。
任务：围绕指定主站记录，理解群聊中新消息的含义，判断它与哪些子站/后台/代理站相关，是否暗示状态变化，并生成适合台账维护的摘要和回复。
只输出 JSON，不要输出 Markdown。
JSON 字段：
- title: string|null，网站名称，不确定则 null
- status: string|null，只能是 待处理/处理中/已处理/搁置/无需处理/null
- summary: string，面向台账的简洁中文摘要，合并既有摘要和新信息，保留关键结论和待办
- notes: string|null，新消息带来的补充信息；如果涉及子域名，写清子域名和上下文
- reply: string|null，发给群里的简短中文回复，一行即可，例如“已更新：example.com｜待处理｜已归并 2 个子站”
- confidence: number，0 到 1

规则：
- site_key 是主站记录 key。related_domains 是本条消息出现的域名/子域名，它们属于同一个主站记录。
- 不要把同一主站的不同子域拆成多条记录。
- 只有消息明确表示状态变化时才给 status；不明确时 status 为 null，保持原状态。
- 普通登记新 URL 时通常是 待处理；明确完成、无需处理、暂停、处理中时再改成对应状态。
"""

INTENT_PROMPT = """你是 Telegram 网站台账机器人的意图识别器。
判断用户是否在和机器人交互。只输出 JSON，不要输出 Markdown。
JSON 字段：
- intent: string，只能是 list/site/status/help/none
- domain: string|null，只有查询或更新某个网站时填写域名
- status: string|null，只能是 待处理/处理中/已处理/搁置/无需处理/null
- notes: string|null，仅当意图为状态更新(status)时，若用户在消息中提供了有关该网站状态更新的补充说明、备注或理由（除状态指示词如“标记处理完成”外），则提取出来作为 notes，否则为 null。
- confidence: number，0 到 1

意图说明：
- list：用户想看网站列表、待办/待处理列表、已处理列表等
- site：用户想查看某个域名的详情、摘要或状态
- status：用户想把某个域名更新成某个状态，并且可能带有对该状态的额外说明
- help：用户询问用法
- none：普通聊天、单纯记录新 URL、无法确定或缺少必要信息
"""


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def analyze_site_message(
        self,
        site_key: str,
        message_text: str,
        related_domains: list[str],
        existing_summary: str = "",
        existing_status: str = "",
        existing_notes: str = "",
        recent_messages: list[str] | None = None,
    ) -> AnalysisResult:
        if not self.api_key:
            return fallback_analysis(site_key, message_text, related_domains)

        user_payload = {
            "site_key": site_key,
            "related_domains": related_domains,
            "message": message_text,
            "existing_summary": existing_summary,
            "existing_status": existing_status,
            "existing_notes": existing_notes,
            "recent_messages": recent_messages or [],
            "allowed_statuses": [item.value for item in SiteStatus],
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(user_payload, ensure_ascii=False),
                            },
                        ],
                        "temperature": 0.2,
                        "thinking": {"type": "disabled"},
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                data = json.loads(content)
        except Exception:
            return fallback_analysis(site_key, message_text, related_domains)

        return coerce_analysis(site_key, message_text, related_domains, data)

    async def classify_bot_intent(self, message_text: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": INTENT_PROMPT},
                            {"role": "user", "content": message_text},
                        ],
                        "temperature": 0,
                        "thinking": {"type": "disabled"},
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                data = json.loads(content)
        except Exception:
            return None

        return data if isinstance(data, dict) else None


def coerce_analysis(
    site_key: str,
    message_text: str,
    related_domains: list[str],
    data: dict[str, Any],
) -> AnalysisResult:
    status = data.get("status")
    normalized_status = normalize_status(str(status)) if status else None
    confidence = data.get("confidence", 0.5)
    try:
        confidence_float = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_float = 0.5

    return AnalysisResult(
        domain=site_key,
        canonical_url=extract_first_url_for_domains(message_text, related_domains),
        title=string_or_none(data.get("title")),
        status=normalized_status,
        summary=str(data.get("summary") or message_text[:240]),
        notes=string_or_none(data.get("notes")),
        confidence=confidence_float,
        reply=string_or_none(data.get("reply")),
    )


def fallback_analysis(site_key: str, message_text: str, related_domains: list[str]) -> AnalysisResult:
    status = infer_status_from_text(message_text)
    return AnalysisResult(
        domain=site_key,
        canonical_url=extract_first_url_for_domains(message_text, related_domains),
        title=None,
        status=status,
        summary=message_text[:240],
        notes=message_text[:500],
        confidence=0.35,
        reply=None,
    )


def infer_status_from_text(text: str) -> str | None:
    lowered = text.lower()
    for token in ("已处理", "处理好了", "处理完", "已完成", "done"):
        if token in lowered:
            return SiteStatus.DONE.value
    for token in ("处理中", "在处理", "doing"):
        if token in lowered:
            return SiteStatus.IN_PROGRESS.value
    for token in ("搁置", "暂停", "先放一放"):
        if token in lowered:
            return SiteStatus.PAUSED.value
    for token in ("无需处理", "不用处理", "不用管"):
        if token in lowered:
            return SiteStatus.NO_ACTION.value
    for token in ("待处理", "待办", "需要看", "需要处理", "todo"):
        if token in lowered:
            return SiteStatus.TODO.value
    return None


def string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
