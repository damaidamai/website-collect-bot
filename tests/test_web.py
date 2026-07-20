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
async def test_dashboard_renders_operational_ui_affordances(tmp_path: Path) -> None:
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

    app = create_app(make_settings(database_path))

    with TestClient(app) as client:
        response = client.get("/?q=example")

    assert response.status_code == 200
    assert 'class="stat' in response.text
    assert 'href="/?status=all&amp;q=example"' in response.text
    assert 'data-live-notice role="status" aria-live="polite"' in response.text
    assert 'data-label="网站"' in response.text
    assert 'rel="noopener noreferrer"' in response.text
    assert 'data-target-status="已处理"' in response.text


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


def test_healthz_does_not_require_dashboard_token(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "sites.sqlite3", web_dashboard_token="secret"))

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_supports_full_site_record_lifecycle(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "sites.sqlite3", api_token="secret"))
    headers = {"X-API-Key": "secret"}

    with TestClient(app) as client:
        denied = client.get("/api/v1/sites")
        created = client.post(
            "/api/v1/sites",
            headers=headers,
            json={
                "domain": "https://admin.example.com/login",
                "title": "Example",
                "summary": "待检查",
            },
        )
        site_id = created.json()["id"]
        listed = client.get("/api/v1/sites?status=待处理&q=example", headers=headers)
        updated = client.patch(
            f"/api/v1/sites/{site_id}",
            headers=headers,
            json={
                "canonical_url": "https://example.com/updated",
                "title": "Example Updated",
                "summary": "已补充摘要",
                "notes": "需要复测",
                "reason": "外部系统更新",
            },
        )
        status_updated = client.patch(
            f"/api/v1/sites/{site_id}/status",
            headers=headers,
            json={"status": "done", "reason": "外部系统已完成"},
        )
        event_created = client.post(
            f"/api/v1/sites/{site_id}/events",
            headers=headers,
            json={"event_type": "external_sync", "content": "已同步到外部系统"},
        )
        history = client.get(f"/api/v1/sites/{site_id}/history", headers=headers)
        events = client.get(f"/api/v1/sites/{site_id}/events", headers=headers)
        deleted = client.delete(f"/api/v1/sites/{site_id}", headers=headers)
        missing = client.get(f"/api/v1/sites/{site_id}", headers=headers)

    assert denied.status_code == 401
    assert denied.json() == {"detail": "invalid api token"}
    assert created.status_code == 201
    assert created.json()["domain"] == "example.com"
    assert listed.status_code == 200
    assert listed.json()["counts"]["全部"] == 1
    assert updated.status_code == 200
    assert updated.json()["canonical_url"] == "https://example.com/updated"
    assert updated.json()["title"] == "Example Updated"
    assert updated.json()["summary"] == "已补充摘要"
    assert status_updated.status_code == 200
    assert status_updated.json()["status"] == SiteStatus.DONE.value
    assert event_created.status_code == 201
    assert history.json()[0]["reason"] == "外部系统已完成"
    assert events.json()[0]["event_type"] == "external_sync"
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "site_id": site_id}
    assert missing.status_code == 404


def test_api_supports_bearer_token_and_validates_updates(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "sites.sqlite3", api_token="secret"))

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sites",
            headers={"Authorization": "Bearer secret"},
            json={"domain": "example.com"},
        )
        site_id = created.json()["id"]
        empty_update = client.patch(
            f"/api/v1/sites/{site_id}", headers={"Authorization": "Bearer secret"}, json={}
        )
        invalid_status = client.patch(
            f"/api/v1/sites/{site_id}/status",
            headers={"Authorization": "Bearer secret"},
            json={"status": "not-a-status"},
        )

    assert created.status_code == 201
    assert empty_update.status_code == 422
    assert invalid_status.status_code == 422


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


async def _seed_site_for_scan(tmp_path: Path) -> tuple[Path, int]:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    site = await storage.upsert_site(
        domain="scan.test",
        canonical_url="https://scan.test",
        title="ScanTarget",
        summary="待检测",
        notes="",
    )
    return database_path, site.id


@pytest.mark.asyncio
async def test_api_requires_token(tmp_path: Path) -> None:
    db, _ = await _seed_site_for_scan(tmp_path)
    app = create_app(Settings(database_path=db, web_dashboard_token="", api_token="secret"))
    with TestClient(app) as client:
        no_token = client.get("/api/sites")
        wrong_token = client.get("/api/sites", headers={"X-API-Token": "wrong"})
        ok = client.get("/api/sites", headers={"X-API-Token": "secret"})
    assert no_token.status_code == 401
    assert wrong_token.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["sites"][0]["domain"] == "scan.test"


@pytest.mark.asyncio
async def test_api_token_not_required_when_unset(tmp_path: Path) -> None:
    db, _ = await _seed_site_for_scan(tmp_path)
    app = create_app(Settings(database_path=db, web_dashboard_token="", api_token=""))
    with TestClient(app) as client:
        resp = client.get("/api/sites")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_scan_writeback_and_detail_render(tmp_path: Path) -> None:
    db, site_id = await _seed_site_for_scan(tmp_path)
    app = create_app(Settings(database_path=db, web_dashboard_token="", api_token="secret"))
    with TestClient(app) as client:
        payload = {
            "scan_status": "done",
            "scan_summary": "发现 2 个中危：暴露 .git 目录、默认登录页",
            "runs": [
                {
                    "tool": "nuclei",
                    "status": "done",
                    "result_json": '{"findings": 2}',
                    "summary": "2 findings",
                },
                {
                    "tool": "httpx",
                    "status": "done",
                    "result_json": '{"code": 200}',
                    "summary": "HTTP 200",
                },
            ],
        }
        resp = client.post(
            f"/api/sites/{site_id}/scan",
            json=payload,
            headers={"X-API-Token": "secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["scan_status"] == "done"

        # detail page should render scan section
        detail = client.get(f"/sites/{site_id}")
    assert detail.status_code == 200
    assert "安全检测" in detail.text
    assert "已检测" in detail.text
    assert "检测明细" in detail.text
    assert "nuclei" in detail.text


@pytest.mark.asyncio
async def test_api_status_update_json(tmp_path: Path) -> None:
    db, site_id = await _seed_site_for_scan(tmp_path)
    app = create_app(Settings(database_path=db, web_dashboard_token="", api_token="secret"))
    with TestClient(app) as client:
        resp = client.post(
            f"/api/sites/{site_id}/status",
            json={"status": "已处理"},
            headers={"X-API-Token": "secret"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "已处理"
