from website_collect_bot.bot import parse_natural_language_intent
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
