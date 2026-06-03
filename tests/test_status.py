from website_collect_bot.deepseek import infer_status_from_text
from website_collect_bot.models import SiteStatus, normalize_status


def test_normalize_status_aliases() -> None:
    assert normalize_status("done") == SiteStatus.DONE.value
    assert normalize_status("待办") == SiteStatus.TODO.value


def test_infer_status_from_text() -> None:
    assert infer_status_from_text("example.com 已经处理好了") == SiteStatus.DONE.value
    assert infer_status_from_text("这个先放到待办") == SiteStatus.TODO.value
