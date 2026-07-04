from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from website_collect_bot.config import Settings
from website_collect_bot.storage import Storage
from website_collect_bot.web import create_app


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

    app = create_app(Settings(database_path=database_path))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "网站记录面板" in response.text
    assert "example.com" in response.text
    assert "待处理" in response.text


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

    app = create_app(Settings(database_path=database_path))

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
