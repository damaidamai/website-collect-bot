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
    assert site.notes == "首次记录"

    updated = await storage.set_status("example.com", SiteStatus.DONE.value, reason="测试", notes="说明或备注")
    assert updated is not None
    assert updated.status == SiteStatus.DONE.value
    assert updated.notes == "首次记录\n说明或备注"


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


async def test_list_sites_sort_order(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "sites.sqlite3")
    await storage.init()

    # Create the first site
    await storage.upsert_site(
        domain="example1.com",
        canonical_url=None,
        title=None,
        summary="1",
        notes=None,
    )

    # Sleep slightly to ensure distinct timestamps
    import asyncio
    await asyncio.sleep(0.05)

    # Create the second site
    await storage.upsert_site(
        domain="example2.com",
        canonical_url=None,
        title=None,
        summary="2",
        notes=None,
    )

    sites = await storage.list_sites()
    assert len(sites) == 2
    # Since it is ASC (oldest first), site1 should be first, site2 second
    assert sites[0].domain == "example1.com"
    assert sites[1].domain == "example2.com"


async def test_init_merges_subdomain_sites(tmp_path: Path) -> None:
    db_path = tmp_path / "sites.sqlite3"
    storage = Storage(db_path)
    await storage.init()

    root = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com",
        title="Example",
        summary="主站摘要",
        notes="主站备注",
        status=SiteStatus.DONE.value,
    )
    child = await storage.upsert_site(
        domain="admin.example.com",
        canonical_url="https://admin.example.com/login",
        title=None,
        summary="后台待处理",
        notes="后台备注",
        status=SiteStatus.TODO.value,
    )
    message_id = await storage.record_message(telegram_message_id=1, chat_id=2, sender_name="u", message_text="m")
    await storage.link_message_to_site(message_id, child.id)

    await storage.init()

    merged = await storage.get_site("example.com")
    assert merged is not None
    assert merged.id == root.id
    assert merged.status == SiteStatus.TODO.value
    assert "admin.example.com" in merged.notes
    assert await storage.get_site("admin.example.com") is None
    assert await storage.recent_site_messages(root.id) == ["m"]
