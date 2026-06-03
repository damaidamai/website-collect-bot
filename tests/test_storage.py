from pathlib import Path

from website_collect_bot.models import SiteStatus
from website_collect_bot.storage import Storage


async def test_upsert_site_and_set_status(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "sites.sqlite3")
    await storage.init()

    site = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com",
        title=None,
        summary="需要检查登录问题",
        notes="首次记录",
    )

    assert site.domain == "example.com"
    assert site.status == SiteStatus.TODO.value

    updated = await storage.set_status("example.com", SiteStatus.DONE.value, reason="测试")
    assert updated is not None
    assert updated.status == SiteStatus.DONE.value


async def test_record_message_and_link_site(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "sites.sqlite3")
    await storage.init()

    site = await storage.upsert_site(
        domain="example.com",
        canonical_url=None,
        title=None,
        summary="摘要",
        notes=None,
    )
    message_id = await storage.record_message(telegram_message_id=1, chat_id=2, sender_name="u", message_text="m")
    await storage.link_message_to_site(message_id, site.id)

    recent = await storage.recent_site_messages(site.id)
    assert recent == ["m"]
