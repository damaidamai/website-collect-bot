from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from website_collect_bot.config import Settings
from website_collect_bot.models import SiteStatus
from website_collect_bot.storage import Storage
from website_collect_bot.web import create_app

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def mcp_settings(database_path: Path, api_token: str = "") -> Settings:
    return Settings(
        database_path=database_path,
        web_dashboard_token="",
        api_token=api_token,
    )


def mcp_call(client: TestClient, name: str, arguments: dict | None = None, headers: dict | None = None) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    response = client.post("/mcp", json=payload, headers={**MCP_HEADERS, **(headers or {})})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "result" in body, body
    result = body["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


@pytest.mark.asyncio
async def test_mcp_crud_and_notes(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    await storage.upsert_site(
        domain="example.com",
        canonical_url="https://example.com",
        title="Example",
        summary="登录页",
        notes="初记",
    )

    app = create_app(mcp_settings(database_path))
    with TestClient(app) as client:
        listed = mcp_call(client, "list_sites", {"query": "example"})
        assert listed["count"] == 1
        assert listed["sites"][0]["domain"] == "example.com"

        created = mcp_call(
            client,
            "create_site",
            {
                "domain": "https://new.test/login",
                "summary": "新站",
                "notes": "待看",
                "status": "处理中",
            },
        )
        assert created["site"]["domain"] == "new.test"
        assert created["site"]["status"] == SiteStatus.IN_PROGRESS.value

        updated = mcp_call(
            client,
            "update_site",
            {"domain": "new.test", "status": "已处理", "summary": "看完了"},
        )
        assert updated["site"]["status"] == SiteStatus.DONE.value
        assert updated["site"]["summary"] == "看完了"

        noted = mcp_call(client, "add_notes", {"domain": "example.com", "notes": "补充说明"})
        assert "初记" in noted["site"]["notes"]
        assert "补充说明" in noted["site"]["notes"]

        detail = mcp_call(client, "get_site", {"domain": "example.com"})
        assert detail["site"]["notes"].endswith("补充说明")

        counts = mcp_call(client, "status_counts")
        assert counts["total"] == 2

        deleted = mcp_call(client, "delete_site", {"domain": "new.test", "confirm": True})
        assert deleted == {"deleted": True, "id": created["site"]["id"], "domain": "new.test"}

        remaining = mcp_call(client, "list_sites", {"query": "new.test"})
        assert remaining["count"] == 0


@pytest.mark.asyncio
async def test_mcp_delete_requires_confirm(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    await storage.upsert_site(
        domain="keep.test",
        canonical_url=None,
        title=None,
        summary="x",
        notes=None,
    )
    app = create_app(mcp_settings(database_path))
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "delete_site", "arguments": {"domain": "keep.test"}},
            },
            headers=MCP_HEADERS,
        )
    body = response.json()
    assert response.status_code == 200
    assert body["result"]["isError"] is True
    site = await storage.get_site("keep.test")
    assert site is not None


@pytest.mark.asyncio
async def test_mcp_requires_token_when_configured(tmp_path: Path) -> None:
    database_path = tmp_path / "sites.sqlite3"
    storage = Storage(database_path)
    await storage.init()
    app = create_app(mcp_settings(database_path, api_token="secret"))
    with TestClient(app) as client:
        denied = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=MCP_HEADERS,
        )
        ok = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={**MCP_HEADERS, "Authorization": "Bearer secret"},
        )
    assert denied.status_code == 401
    assert ok.status_code == 200
    names = {tool["name"] for tool in ok.json()["result"]["tools"]}
    assert names == {
        "list_sites",
        "get_site",
        "create_site",
        "update_site",
        "add_notes",
        "delete_site",
        "status_counts",
    }
