from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from website_collect_bot.extract import canonical_site_key, normalize_domain, normalize_url
from website_collect_bot.models import SiteRecord, normalize_status
from website_collect_bot.storage import Storage

MCP_INSTRUCTIONS = """\
网站收集记录库。用这些工具查询和改站点：列表/搜索、查看详情、新增、改状态、改摘要、追加或覆盖备注、删除。
状态只能是：待处理、处理中、已处理、搁置、无需处理。
定位站点优先传 domain；也可以传 site_id。删除必须 confirm=true。
"""

STATUS_HINT = "待处理 / 处理中 / 已处理 / 搁置 / 无需处理"


def site_payload(site: SiteRecord) -> dict[str, Any]:
    return {
        "id": site.id,
        "domain": site.domain,
        "canonical_url": site.canonical_url,
        "title": site.title,
        "status": site.status,
        "summary": site.summary,
        "notes": site.notes,
        "scan_status": site.scan_status,
        "scan_summary": site.scan_summary,
        "scanned_at": site.scanned_at.isoformat() if site.scanned_at else None,
        "first_seen_at": site.first_seen_at.isoformat(),
        "updated_at": site.updated_at.isoformat(),
    }


def domain_key(value: str) -> str:
    return canonical_site_key(normalize_domain(value))


class PathRewriteASGI:
    """Serve one ASGI app at a fixed inner path, ignoring the outer URL path."""

    def __init__(self, app: ASGIApp, path: str = "/") -> None:
        self.app = app
        self.path = path
        self.raw_path = path.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope = dict(scope)
            scope["path"] = self.path
            scope["raw_path"] = self.raw_path
        await self.app(scope, receive, send)


def create_mcp_server(storage: Storage) -> MCPServer:
    mcp = MCPServer(
        name="website-collect",
        title="Website Collect",
        instructions=MCP_INSTRUCTIONS,
    )

    async def resolve_site(site_id: int | None, domain: str | None) -> SiteRecord:
        if site_id is not None:
            site = await storage.get_site_by_id(site_id)
        elif domain and domain.strip():
            key = domain_key(domain)
            site = await storage.get_site(key)
            if site is None:
                site = await storage.get_site(normalize_domain(domain))
        else:
            raise ValueError("需要提供 site_id 或 domain")
        if site is None:
            raise ValueError("站点不存在")
        return site

    @mcp.tool()
    async def list_sites(
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """列出或搜索网站。可按状态筛选，query 会匹配域名/标题/摘要/备注。默认最久未更新的在前。"""
        selected = None
        if status:
            selected = normalize_status(status)
            if selected is None:
                raise ValueError(f"未知状态：{status}。可用：{STATUS_HINT}")
        capped = max(1, min(limit, 200))
        sites = await storage.search_sites(status=selected, query=query, limit=capped)
        counts = await storage.status_counts()
        return {
            "total_by_status": counts,
            "count": len(sites),
            "sites": [site_payload(site) for site in sites],
        }

    @mcp.tool()
    async def get_site(
        site_id: int | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """查看单个站点详情，包含备注、最近消息、事件和状态历史。"""
        site = await resolve_site(site_id, domain)
        messages = await storage.site_messages(site.id)
        events = await storage.site_events(site.id)
        history = await storage.status_history(site.id)
        return {
            "site": site_payload(site),
            "messages": messages,
            "events": events,
            "status_history": history,
        }

    @mcp.tool()
    async def create_site(
        domain: str,
        canonical_url: str | None = None,
        title: str | None = None,
        summary: str = "",
        notes: str = "",
        status: str | None = None,
    ) -> dict[str, Any]:
        """新增站点。域名已存在时会更新传入的非空摘要/备注，并可选改状态。"""
        key = domain_key(domain)
        if not key:
            raise ValueError("domain 无效")
        selected = None
        if status:
            selected = normalize_status(status)
            if selected is None:
                raise ValueError(f"未知状态：{status}。可用：{STATUS_HINT}")
        url = canonical_url or normalize_url(domain)
        site = await storage.upsert_site(
            domain=key,
            canonical_url=url,
            title=title,
            summary=summary,
            notes=notes or None,
            status=selected,
        )
        await storage.add_event(site.id, "mcp_upsert", f"MCP 写入 {site.domain}")
        return {"site": site_payload(site)}

    @mcp.tool()
    async def update_site(
        site_id: int | None = None,
        domain: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """更新已有站点的状态、摘要或备注。备注是覆盖写入；要追加请用 add_notes。"""
        site = await resolve_site(site_id, domain)
        next_status = None
        if status:
            next_status = normalize_status(status)
            if next_status is None:
                raise ValueError(f"未知状态：{status}。可用：{STATUS_HINT}")
        if next_status is None and summary is None and notes is None:
            raise ValueError("至少提供 status、summary、notes 之一")
        updated = await storage.update_site_by_id(
            site.id,
            status=next_status,
            summary=summary,
            notes=notes,
            reason="MCP 更新",
        )
        if updated is None:
            raise ValueError("站点不存在")
        return {"site": site_payload(updated)}

    @mcp.tool()
    async def add_notes(
        notes: str,
        site_id: int | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """给站点追加备注，不会覆盖已有备注。"""
        site = await resolve_site(site_id, domain)
        updated = await storage.append_notes(site.id, notes, reason="MCP 追加备注")
        if updated is None:
            raise ValueError("站点不存在")
        return {"site": site_payload(updated)}

    @mcp.tool()
    async def delete_site(
        site_id: int | None = None,
        domain: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """删除站点及其关联事件/历史。必须 confirm=true。"""
        if not confirm:
            raise ValueError("删除需要 confirm=true")
        site = await resolve_site(site_id, domain)
        deleted = await storage.delete_site_by_id(site.id)
        if deleted is None:
            raise ValueError("站点不存在")
        return {"deleted": True, "id": deleted.id, "domain": deleted.domain}

    @mcp.tool()
    async def status_counts() -> dict[str, Any]:
        """各处理状态的站点数量。"""
        counts = await storage.status_counts()
        return {"total": sum(counts.values()), "counts": counts}

    return mcp


def build_mcp_asgi(mcp: MCPServer) -> Starlette:
    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def mount_mcp(app: Any, mcp_asgi: Starlette) -> None:
    rewritten = PathRewriteASGI(mcp_asgi)
    app.router.routes.append(Route("/mcp", rewritten, methods=["GET", "POST", "DELETE"]))
    app.mount("/mcp", mcp_asgi)
