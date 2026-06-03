from website_collect_bot.intent import (
    coerce_ai_intent,
    parse_natural_language_intent,
    should_use_ai_intent,
)
from website_collect_bot.models import SiteStatus


def test_parse_todo_list_with_bot_mention() -> None:
    intent = parse_natural_language_intent("@cute73_bot 待处理列表")

    assert intent is not None
    assert intent.name == "list"
    assert intent.status == SiteStatus.TODO.value


def test_parse_all_sites_list() -> None:
    intent = parse_natural_language_intent("全部网站")

    assert intent is not None
    assert intent.name == "list"
    assert intent.status is None


def test_parse_site_detail_request() -> None:
    intent = parse_natural_language_intent("帮我查一下 example.com 的状态")

    assert intent is not None
    assert intent.name == "site"
    assert intent.domain == "example.com"


def test_parse_status_update_request() -> None:
    intent = parse_natural_language_intent("把 example.com 标为已处理")

    assert intent is not None
    assert intent.name == "status"
    assert intent.domain == "example.com"
    assert intent.status == SiteStatus.DONE.value


def test_should_use_ai_intent_for_bot_mention() -> None:
    assert should_use_ai_intent("@cute73_bot 还有哪些没搞完")


def test_coerce_ai_list_intent() -> None:
    intent = coerce_ai_intent(
        {
            "intent": "list",
            "domain": None,
            "status": "待处理",
            "confidence": 0.92,
        }
    )

    assert intent is not None
    assert intent.name == "list"
    assert intent.status == SiteStatus.TODO.value


def test_coerce_ai_intent_rejects_low_confidence() -> None:
    intent = coerce_ai_intent(
        {
            "intent": "status",
            "domain": "example.com",
            "status": "已处理",
            "confidence": 0.3,
        }
    )

    assert intent is None


def test_coerce_ai_intent_with_notes() -> None:
    intent = coerce_ai_intent(
        {
            "intent": "status",
            "domain": "example.com",
            "status": "已处理",
            "notes": "全局控制错误登录次数无法爆破",
            "confidence": 0.9,
        }
    )

    assert intent is not None
    assert intent.name == "status"
    assert intent.domain == "example.com"
    assert intent.status == SiteStatus.DONE.value
    assert intent.notes == "全局控制错误登录次数无法爆破"
