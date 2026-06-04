from types import SimpleNamespace

from website_collect_bot.bot import (
    analysis_message_text,
    group_domains_by_site_key,
    join_context,
    message_reply_text,
)
from website_collect_bot.extract import extract_domains
from website_collect_bot.intent import find_status_in_text, should_use_ai_intent
from website_collect_bot.models import SiteStatus


def test_group_domains_by_site_key() -> None:
    grouped = group_domains_by_site_key(
        [
            "admin.example.com",
            "agent.example.com",
            "other.test.com",
        ]
    )

    assert grouped == {
        "example.com": ["admin.example.com", "agent.example.com"],
        "test.com": ["other.test.com"],
    }


def test_reply_context_supplies_domain_for_followup() -> None:
    message = SimpleNamespace(
        text="登录限制控制很严，无法使用代理池进行爆破，放弃。",
        reply_to_message=SimpleNamespace(
            text="🌐 ragnaroksys.com\nURL：https://agent.ragnaroksys.com/login.html\n状态：待处理"
        ),
    )

    reply_context = message_reply_text(message)
    combined = join_context(message.text, reply_context)

    assert extract_domains(combined) == ["agent.ragnaroksys.com", "ragnaroksys.com"]
    assert group_domains_by_site_key(extract_domains(combined)) == {
        "ragnaroksys.com": ["agent.ragnaroksys.com", "ragnaroksys.com"]
    }
    assert find_status_in_text(message.text) == SiteStatus.PAUSED.value
    assert should_use_ai_intent(combined)
    assert analysis_message_text(message.text, reply_context).startswith("当前回复：登录限制控制很严")
