from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SiteStatus(StrEnum):
    TODO = "待处理"
    IN_PROGRESS = "处理中"
    DONE = "已处理"
    PAUSED = "搁置"
    NO_ACTION = "无需处理"

    @classmethod
    def values(cls) -> set[str]:
        return {item.value for item in cls}


STATUS_ALIASES = {
    "todo": SiteStatus.TODO.value,
    "待办": SiteStatus.TODO.value,
    "待处理": SiteStatus.TODO.value,
    "处理中": SiteStatus.IN_PROGRESS.value,
    "doing": SiteStatus.IN_PROGRESS.value,
    "done": SiteStatus.DONE.value,
    "完成": SiteStatus.DONE.value,
    "已完成": SiteStatus.DONE.value,
    "已处理": SiteStatus.DONE.value,
    "处理完": SiteStatus.DONE.value,
    "搁置": SiteStatus.PAUSED.value,
    "暂停": SiteStatus.PAUSED.value,
    "无需处理": SiteStatus.NO_ACTION.value,
    "不用处理": SiteStatus.NO_ACTION.value,
}


def normalize_status(value: str) -> str | None:
    cleaned = value.strip()
    if cleaned in SiteStatus.values():
        return cleaned
    return STATUS_ALIASES.get(cleaned.lower())


@dataclass(frozen=True)
class SiteRecord:
    id: int
    domain: str
    canonical_url: str | None
    title: str | None
    status: str
    summary: str
    notes: str
    first_seen_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AnalysisResult:
    domain: str
    canonical_url: str | None
    title: str | None
    status: str | None
    summary: str
    notes: str | None
    confidence: float
