from __future__ import annotations

import json
from typing import Any

import httpx

from website_collect_bot.extract import extract_first_url_for_domain
from website_collect_bot.models import AnalysisResult, SiteStatus, normalize_status


SYSTEM_PROMPT = """你是一个 Telegram 群聊信息整理助手。
任务：围绕指定网站，判断新消息提供了什么信息，是否暗示状态变化，并生成简洁摘要。
只输出 JSON，不要输出 Markdown。
JSON 字段：
- title: string|null，网站名称，不确定则 null
- status: string|null，只能是 待处理/处理中/已处理/搁置/无需处理/null
- summary: string，面向台账的简洁中文摘要，保留关键结论和待办
- notes: string|null，新消息带来的补充信息
- confidence: number，0 到 1
"""

INTENT_PROMPT = """你是 Telegram 网站台账机器人的意图识别器。
判断用户是否在和机器人交互。只输出 JSON，不要输出 Markdown。
JSON 字段：
- intent: string，只能是 list/site/status/help/none
- domain: string|null，只有查询或更新某个网站时填写域名
- status: string|null，只能是 待处理/处理中/已处理/搁置/无需处理/null
- confidence: number，0 到 1

意图说明：
- list：用户想看网站列表、待办/待处理列表、已处理列表等
- site：用户想查看某个域名的详情、摘要或状态
- status：用户想把某个域名更新成某个状态
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
        domain: str,
        message_text: str,
        existing_summary: str = "",
        recent_messages: list[str] | None = None,
    ) -> AnalysisResult:
        if not self.api_key:
            return fallback_analysis(domain, message_text)

        user_payload = {
            "domain": domain,
            "message": message_text,
            "existing_summary": existing_summary,
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
            return fallback_analysis(domain, message_text)

        return coerce_analysis(domain, message_text, data)

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


def coerce_analysis(domain: str, message_text: str, data: dict[str, Any]) -> AnalysisResult:
    status = data.get("status")
    normalized_status = normalize_status(str(status)) if status else None
    confidence = data.get("confidence", 0.5)
    try:
        confidence_float = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_float = 0.5

    return AnalysisResult(
        domain=domain,
        canonical_url=extract_first_url_for_domain(message_text, domain),
        title=string_or_none(data.get("title")),
        status=normalized_status,
        summary=str(data.get("summary") or message_text[:240]),
        notes=string_or_none(data.get("notes")),
        confidence=confidence_float,
    )


def fallback_analysis(domain: str, message_text: str) -> AnalysisResult:
    status = infer_status_from_text(message_text)
    return AnalysisResult(
        domain=domain,
        canonical_url=extract_first_url_for_domain(message_text, domain),
        title=None,
        status=status,
        summary=message_text[:240],
        notes=message_text[:500],
        confidence=0.35,
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
