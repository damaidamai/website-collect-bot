from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from website_collect_bot.config import Settings
from website_collect_bot.models import SiteStatus
from website_collect_bot.storage import Storage
from website_collect_bot.web import create_app


def make_settings(database_path: Path) -> Settings:
    return Settings(database_path=database_path, web_dashboard_token="")


@pytest.mark.asyncio
async def test_dashboard_lists_sites(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com/login",
        title="Example",
        summary="登录页",
        notes="需要测试",
    )
    await storage.upsert_site(
        domain="finished.test",
        canonical_url="https://finished.test",
        title="Done",
        summary="已完成站点",
        notes="",
        status=SiteStatus.DONE.value,
    )

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        default_response = client.get("/")
        all_response = client.get("/?status=all")

    assert default_response.status_code == 200
    assert "网站记录面板" in default_response.text
    assert "example.com" in default_response.text
    assert "finished.test" not in default_response.text
    assert "待处理" in default_response.text
    assert all_response.status_code == 200
    assert "finished.test" in all_response.text


@pytest.mark.asyncio
async def test_dashboard_lists_sites_by_oldest_update_first(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    await storage.upsert_site(
        domain="older.test",
        canonical_url="https://older.test",
        title="Older",
        summary="较早记录",
        notes="",
    )
    await storage.upsert_site(
        domain="newer.test",
        canonical_url="https://newer.test",
        title="Newer",
        summary="较新记录",
        notes="",
    )

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.text.index("older.test") < response.text.index("newer.test")


@pytest.mark.asyncio
async def test_dashboard_detail_shows_messages(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    site = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com/login",
        title="Example",
        summary="登录页",
        notes="",
    )
    message_id = await storage.record_message(12, 34, "cute73", "https://example.com/login 看一下")
    await storage.link_message_to_site(message_id, site.id)

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.get(f"/sites/{site.id}")

    assert response.status_code == 200
    assert "基本信息" in response.text
    assert "关联消息" in response.text
    assert "cute73" in response.text


def test_dashboard_token_required(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "sites.sqlite3", web_dashboard_token="secret"))

    with TestClient(app) as client:
        denied = client.get("/")
        allowed = client.get("/?token=secret")
        cookie_allowed = client.get("/")

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert cookie_allowed.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_updates_status(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    site = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com/login",
        title="Example",
        summary="登录页",
        notes="",
    )

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.post(
            f"/sites/{site.id}/status",
            data={"status": SiteStatus.DONE.value, "return_to": f"/sites/{site.id}"},
            follow_redirects=False,
        )

    updated = await storage.get_site_by_id(site.id)
    history = await storage.status_history(site.id)

    assert response.status_code == 303
    assert response.headers["location"] == f"/sites/{site.id}"
    assert updated is not None
    assert updated.status == SiteStatus.DONE.value
    assert history[0]["reason"] == "Web 面板手动更新状态"


@pytest.mark.asyncio
async def test_dashboard_updates_status_with_fetch_response(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    site = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com/login",
        title="Example",
        summary="登录页",
        notes="",
    )

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.post(
            f"/sites/{site.id}/status",
            data={"status": SiteStatus.NO_ACTION.value, "return_to": "/"},
            headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        )

    payload = response.json()
    updated = await storage.get_site_by_id(site.id)

    assert response.status_code == 200
    assert payload["site_id"] == site.id
    assert payload["status"] == SiteStatus.NO_ACTION.value
    assert payload["counts"]["全部"] == 1
    assert payload["counts"][SiteStatus.NO_ACTION.value] == 1
    assert updated is not None
    assert updated.status == SiteStatus.NO_ACTION.value


@pytest.mark.asyncio
async def test_dashboard_updates_summary_and_notes(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    site = await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com/login",
        title="Example",
        summary="登录页",
        notes="",
    )

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.post(
            f"/sites/{site.id}/content",
            data={
                "summary": "新的摘要",
                "notes": "新的备注",
                "return_to": f"/sites/{site.id}",
            },
            follow_redirects=False,
        )

    updated = await storage.get_site_by_id(site.id)
    events = await storage.site_events(site.id)

    assert response.status_code == 303
    assert updated is not None
    assert updated.summary == "新的摘要"
    assert updated.notes == "新的备注"
    assert events[0]["event_type"] == "web_update"
