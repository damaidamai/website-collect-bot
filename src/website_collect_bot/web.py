from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from website_collect_bot.config import Settings, get_settings
from website_collect_bot.models import SiteRecord, SiteStatus, normalize_status
from website_collect_bot.storage import Storage


STATUS_ORDER = [
    SiteStatus.TODO.value,
    SiteStatus.IN_PROGRESS.value,
    SiteStatus.DONE.value,
    SiteStatus.PAUSED.value,
    SiteStatus.NO_ACTION.value,
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    storage = Storage(settings.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await storage.init()
        yield

    app = FastAPI(title="Website Collect Dashboard", lifespan=lifespan)

    @app.middleware("http")
    async def dashboard_auth(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)

        token = settings.web_dashboard_token.strip()
        if not token:
            return await call_next(request)

        query_token = request.query_params.get("token", "")
        cookie_token = request.cookies.get("dashboard_token", "")
        if query_token == token or cookie_token == token:
            response = await call_next(request)
            if query_token == token:
                response.set_cookie(
                    "dashboard_token",
                    token,
                    httponly=True,
                    samesite="lax",
                    max_age=60 * 60 * 24 * 30,
                )
            return response

        return HTMLResponse(
            page(
                "需要访问 Token",
                """
                <section class="empty">
                  <h1>需要访问 Token</h1>
                  <p>请使用带 <code>?token=...</code> 的链接访问，验证后浏览器会保存 Cookie。</p>
                </section>
                """,
            ),
            status_code=401,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        status: str | None = None,
        q: str | None = None,
    ) -> HTMLResponse:
        selected_status = selected_status_from_query(status)
        if status and selected_status is None and status.strip().lower() not in {"all", "全部"}:
            raise HTTPException(status_code=400, detail="unknown status")

        counts = await storage.status_counts()
        sites = await storage.search_sites(status=selected_status, query=q, limit=200)
        total = sum(counts.values())
        content = render_index(
            sites=sites,
            counts=counts,
            total=total,
            selected_status=selected_status,
            query=q or "",
            return_to=current_path(request),
        )
        return HTMLResponse(page("网站记录", content))

    @app.get("/sites/{site_id}", response_class=HTMLResponse)
    async def detail(request: Request, site_id: int) -> HTMLResponse:
        site = await storage.get_site_by_id(site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="site not found")
        messages = await storage.site_messages(site_id)
        events = await storage.site_events(site_id)
        history = await storage.status_history(site_id)
        return HTMLResponse(
            page(site.domain, render_detail(site, messages, events, history, current_path(request)))
        )

    @app.post("/sites/{site_id}/status")
    async def update_status(site_id: int, request: Request) -> Response:
        form = await request.form()
        status = normalize_status(str(form.get("status", "")))
        if status is None:
            raise HTTPException(status_code=400, detail="unknown status")

        site = await storage.update_site_by_id(
            site_id,
            status=status,
            reason="Web 面板手动更新状态",
        )
        if site is None:
            raise HTTPException(status_code=404, detail="site not found")
        if is_fetch_request(request):
            counts = await storage.status_counts()
            return JSONResponse(
                {
                    "site_id": site.id,
                    "status": site.status,
                    "counts": counts_with_total(counts),
                }
            )
        return RedirectResponse(safe_return_to(form.get("return_to"), f"/sites/{site_id}"), status_code=303)

    @app.post("/sites/{site_id}/content")
    async def update_content(site_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        summary = str(form.get("summary", "")).strip()
        notes = str(form.get("notes", "")).strip()
        site = await storage.update_site_by_id(
            site_id,
            summary=summary,
            notes=notes,
            reason="Web 面板更新摘要/备注",
        )
        if site is None:
            raise HTTPException(status_code=404, detail="site not found")
        return RedirectResponse(safe_return_to(form.get("return_to"), f"/sites/{site_id}"), status_code=303)

    return app


def page(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - Website Collect</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-soft: #dff3ef;
      --accent-line: #8bd5c8;
      --danger: #b42318;
      --warn: #b54708;
      --ok: #067647;
      --info: #175cd3;
      --shadow: 0 1px 2px rgba(16, 24, 40, .08), 0 8px 20px rgba(16, 24, 40, .04);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible {{
      outline: 3px solid rgba(15, 118, 110, .22);
      outline-offset: 2px;
    }}
    .shell {{ max-width: 1240px; margin: 0 auto; padding: 20px; }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .brand {{ font-size: 24px; font-weight: 750; letter-spacing: 0; }}
    .muted {{ color: var(--muted); }}
    .toolbar {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 8px;
      margin: 10px 0 14px;
    }}
    .stat, .panel, .empty, .notice {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .stat {{
      display: block;
      padding: 10px 12px;
      color: var(--text);
      transition: border-color .15s ease, transform .15s ease, background .15s ease;
    }}
    .stat:hover {{ text-decoration: none; border-color: var(--accent-line); transform: translateY(-1px); }}
    .stat.active {{ border-color: var(--accent); background: var(--accent-soft); }}
    .stat strong {{ display: block; font-size: 22px; line-height: 1.15; }}
    .stat span {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    .filters {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin: 10px 0 14px;
    }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 7px; }}
    .tab {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 31px;
      padding: 5px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      font-size: 14px;
    }}
    .tab.active {{ border-color: var(--accent); background: var(--accent-soft); color: #0b5b54; }}
    form.search {{ display: flex; gap: 7px; margin-left: auto; align-items: center; }}
    input[type="search"] {{
      width: min(360px, 60vw);
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 10px;
      font: inherit;
      background: var(--panel);
    }}
    textarea {{
      width: 100%;
      min-height: 110px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      font: inherit;
      background: var(--panel);
    }}
    button {{
      min-height: 34px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      padding: 6px 11px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      cursor: pointer;
    }}
    button:disabled {{ cursor: progress; }}
    button.secondary {{ border-color: var(--line); background: var(--panel); color: var(--text); }}
    button.danger {{ border-color: var(--danger); background: var(--danger); }}
    button.warn {{ border-color: var(--warn); background: var(--warn); }}
    button.ok {{ border-color: var(--ok); background: var(--ok); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .quick-actions {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 5px;
      align-items: center;
      min-width: 0;
    }}
    .quick-actions form {{ margin: 0; }}
    .quick-actions button {{
      width: 100%;
      min-height: 28px;
      padding: 3px 6px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      line-height: 1.2;
      white-space: nowrap;
    }}
    .quick-actions button.is-current {{
      border-color: var(--line);
      background: #f8fafc;
      color: var(--muted);
    }}
    .edit-form {{ display: grid; gap: 10px; }}
    .edit-form label {{ display: grid; gap: 5px; color: var(--muted); font-size: 14px; }}
    .panel {{ overflow: hidden; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      table-layout: fixed;
    }}
    .sites-table col:nth-child(1) {{ width: 30%; }}
    .sites-table col:nth-child(2) {{ width: 9%; }}
    .sites-table col:nth-child(3) {{ width: 30%; }}
    .sites-table col:nth-child(4) {{ width: 10%; }}
    .sites-table col:nth-child(5) {{ width: 21%; }}
    th, td {{
      padding: 9px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      font-size: 13px;
    }}
    th {{ color: var(--muted); font-weight: 600; background: #fbfcfd; }}
    tr:last-child td {{ border-bottom: 0; }}
    .domain {{ font-weight: 700; }}
    .summary {{ color: #344054; }}
    .summary-note {{
      margin-top: 4px;
      color: var(--muted);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .url-line {{
      margin-top: 2px;
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .cell-action {{ padding-right: 10px; }}
    .status {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
      background: #eef2f6;
      color: #344054;
    }}
    .status.todo {{ background: #fee4e2; color: var(--danger); }}
    .status.doing {{ background: #fef0c7; color: var(--warn); }}
    .status.done {{ background: #dcfae6; color: var(--ok); }}
    .status.paused {{ background: #f2f4f7; color: #475467; }}
    .status.none {{ background: #dbeafe; color: var(--info); }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .section {{ padding: 14px; }}
    .section h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .kv {{ display: grid; grid-template-columns: 100px 1fr; gap: 8px 12px; }}
    .text-block {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    .message, .event {{ border-top: 1px solid var(--line); padding: 12px 0; }}
    .message:first-child, .event:first-child {{ border-top: 0; padding-top: 0; }}
    .empty {{ padding: 28px; text-align: center; color: var(--muted); }}
    .notice {{
      display: none;
      margin-bottom: 12px;
      padding: 10px 12px;
      border-color: var(--accent-line);
      color: #0b5b54;
      background: #f0faf8;
    }}
    .notice.is-visible {{ display: block; }}
    .row-flash {{ animation: rowFlash 1.4s ease; }}
    @keyframes rowFlash {{
      0% {{ background: #f0faf8; }}
      100% {{ background: transparent; }}
    }}
    @media (max-width: 820px) {{
      .shell {{ padding: 16px; }}
      .topbar, .toolbar, form.search {{ align-items: stretch; flex-direction: column; }}
      form.search {{ width: 100%; margin-left: 0; }}
      input[type="search"] {{ width: 100%; }}
      .grid {{ grid-template-columns: 1fr; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); padding: 10px 0; }}
      td {{
        border-bottom: 0;
        padding: 6px 14px;
        display: grid;
        grid-template-columns: 82px 1fr;
        gap: 10px;
      }}
      td::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 12px;
        font-weight: 600;
      }}
      td[data-label="网站"] {{ display: block; }}
      td[data-label="网站"]::before {{ display: block; margin-bottom: 3px; }}
      .quick-actions {{ grid-template-columns: repeat(3, minmax(82px, 1fr)); min-width: 0; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    {content}
  </main>
  {client_script()}
</body>
</html>"""


def render_index(
    sites: list[SiteRecord],
    counts: dict[str, int],
    total: int,
    selected_status: str | None,
    query: str,
    return_to: str,
) -> str:
    stats = [stat_card("全部", total, selected_status is None, None, query)]
    stats.extend(
        stat_card(status, counts.get(status, 0), selected_status == status, status, query)
        for status in STATUS_ORDER
    )
    tabs = [tab_link("全部", None, selected_status, query)]
    tabs.extend(tab_link(status, status, selected_status, query) for status in STATUS_ORDER)
    rows = "\n".join(render_site_row(site, return_to) for site in sites)
    table = (
        f"""
        <div class="panel">
          <table class="sites-table">
            <colgroup>
              <col>
              <col>
              <col>
              <col>
              <col>
            </colgroup>
            <thead>
              <tr>
                <th>网站</th>
                <th>状态</th>
                <th>摘要</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
        if sites
        else '<section class="empty">没有匹配的记录</section>'
    )
    selected_input_value = selected_status or "all"
    selected_input = f'<input type="hidden" name="status" value="{escape(selected_input_value)}">'
    clear_link = (
        '<a class="tab" href="/">清空</a>'
        if query or selected_status is None
        else ""
    )
    active_label = selected_status or "全部"
    return f"""
    <div class="topbar">
      <div>
        <div class="brand">网站记录面板</div>
        <div class="muted">当前查看：{escape(active_label)} · 共 {total} 条记录 · 最多显示 200 条</div>
      </div>
      <div class="toolbar">
        <a class="tab" href="{escape(return_to)}">刷新</a>
        <a class="tab" href="/?status=all">全部记录</a>
      </div>
    </div>
    <div class="notice" data-live-notice role="status" aria-live="polite"></div>
    <section class="stats">{''.join(stats)}</section>
    <section class="filters">
      <nav class="tabs">{''.join(tabs)}</nav>
      <form class="search" method="get" action="/">
        {selected_input}
        <input type="search" name="q" value="{escape(query)}" placeholder="搜索域名、URL、摘要或备注">
        <button type="submit">搜索</button>
        {clear_link}
      </form>
    </section>
    {table}
    """


def render_detail(
    site: SiteRecord,
    messages: list[dict[str, object]],
    events: list[dict[str, str]],
    history: list[dict[str, str]],
    return_to: str,
) -> str:
    message_items = "".join(
        f"""
        <div class="message">
          <div class="muted">{escape(str(item["created_at"]))} · {escape(str(item["sender_name"]))}
            · #{escape(str(item["telegram_message_id"]))}</div>
          <div class="text-block">{escape(str(item["message_text"]))}</div>
        </div>
        """
        for item in messages
    )
    event_items = "".join(
        f"""
        <div class="event">
          <div class="muted">{escape(item["created_at"])} · {escape(item["event_type"])}</div>
          <div class="text-block">{escape(item["content"])}</div>
        </div>
        """
        for item in events
    )
    history_rows = "".join(
        f"""
        <tr>
          <td>{escape(item["created_at"])}</td>
          <td>{escape(item["old_status"] or "-")} → {escape(item["new_status"])}</td>
          <td>{escape(item["reason"])}</td>
        </tr>
        """
        for item in history
    )
    return f"""
    <div class="topbar">
      <div>
        <a href="/">← 返回列表</a>
        <div class="brand">{escape(site.domain)}</div>
      </div>
      {status_badge(site.status)}
    </div>
    <section class="panel section">
      <h2>操作</h2>
      <div class="actions">
        {status_form(site.id, SiteStatus.IN_PROGRESS.value, "标记处理中", return_to, "warn", site.status)}
        {status_form(site.id, SiteStatus.DONE.value, "标记已处理", return_to, "ok", site.status)}
        {status_form(site.id, SiteStatus.NO_ACTION.value, "标记无需处理", return_to, "secondary", site.status)}
        {status_form(site.id, SiteStatus.PAUSED.value, "搁置", return_to, "secondary", site.status)}
        {status_form(site.id, SiteStatus.TODO.value, "退回待处理", return_to, "danger", site.status)}
      </div>
    </section>
    <section class="panel section">
      <h2>基本信息</h2>
      <div class="kv">
        <div class="muted">URL</div>
        <div>{site_link(site)}</div>
        <div class="muted">标题</div>
        <div>{escape(site.title or "-")}</div>
        <div class="muted">首次记录</div>
        <div>{fmt_dt(site.first_seen_at)}</div>
        <div class="muted">最近更新</div>
        <div>{fmt_dt(site.updated_at)}</div>
        <div class="muted">摘要</div>
        <div class="text-block">{escape(site.summary or "-")}</div>
        <div class="muted">备注</div>
        <div class="text-block">{escape(site.notes or "-")}</div>
      </div>
    </section>
    <section class="panel section" style="margin-top: 16px;">
      <h2>更新摘要</h2>
      <form class="edit-form" method="post" action="/sites/{site.id}/content">
        <input type="hidden" name="return_to" value="{escape(return_to)}">
        <label>
          摘要
          <textarea name="summary">{escape(site.summary)}</textarea>
        </label>
        <label>
          备注
          <textarea name="notes">{escape(site.notes)}</textarea>
        </label>
        <div><button type="submit">保存摘要和备注</button></div>
      </form>
    </section>
    <section class="grid" style="margin-top: 16px;">
      <div class="panel section">
        <h2>关联消息</h2>
        {message_items or '<div class="muted">暂无消息</div>'}
      </div>
      <div>
        <div class="panel section">
          <h2>状态历史</h2>
          <table>
            <thead><tr><th>时间</th><th>变更</th><th>原因</th></tr></thead>
            <tbody>{history_rows or '<tr><td colspan="3" class="muted">暂无状态变更</td></tr>'}</tbody>
          </table>
        </div>
        <div class="panel section" style="margin-top: 16px;">
          <h2>事件</h2>
          {event_items or '<div class="muted">暂无事件</div>'}
        </div>
      </div>
    </section>
    """


def render_site_row(site: SiteRecord, return_to: str) -> str:
    summary = site.summary or "-"
    note = site.notes if site.notes and site.notes != site.summary else ""
    return f"""
    <tr data-site-row="{site.id}">
      <td data-label="网站">
        <a class="domain" href="/sites/{site.id}">{escape(site.domain)}</a>
        <div class="muted url-line">{site_link(site)}</div>
      </td>
      <td data-label="状态">{status_badge(site.status)}</td>
      <td class="summary" data-label="摘要">
        <div>{escape(summary)}</div>
        {f'<div class="summary-note">{escape(note)}</div>' if note else ''}
      </td>
      <td data-label="更新">{fmt_dt(site.updated_at)}</td>
      <td class="cell-action" data-label="操作">
        <div class="quick-actions">
          {status_form(site.id, SiteStatus.DONE.value, "已处理", return_to, "ok", site.status)}
          {status_form(site.id, SiteStatus.NO_ACTION.value, "无需处理", return_to, "secondary", site.status)}
          {status_form(site.id, SiteStatus.IN_PROGRESS.value, "处理中", return_to, "warn", site.status)}
        </div>
      </td>
    </tr>
    """


def stat_card(label: str, value: int, active: bool, status_value: str | None, query: str) -> str:
    params: dict[str, str] = {"status": status_value or "all"}
    if query:
        params["q"] = query
    href = "/?" + urlencode(params)
    active_class = " active" if active else ""
    return (
        f'<a class="stat{active_class}" href="{escape(href, quote=True)}" data-stat="{escape(label)}">'
        f'<span class="muted">{escape(label)}<span>查看</span></span><strong>{value}</strong></a>'
    )


def tab_link(label: str, value: str | None, selected: str | None, query: str) -> str:
    params: dict[str, str] = {}
    if value:
        params["status"] = value
    else:
        params["status"] = "all"
    if query:
        params["q"] = query
    href = "/"
    if params:
        href += "?" + urlencode(params)
    active = " active" if value == selected else ""
    if value is None and selected is None:
        active = " active"
    return f'<a class="tab{active}" href="{escape(href, quote=True)}">{escape(label)}</a>'


def client_script() -> str:
    return """
  <script>
    (() => {
      const statusClasses = {
        "待处理": "todo",
        "处理中": "doing",
        "已处理": "done",
        "搁置": "paused",
        "无需处理": "none",
      };

      function currentListStatus() {
        const params = new URLSearchParams(window.location.search);
        const status = params.get("status");
        if (!status) return "待处理";
        if (status === "all" || status === "全部") return "all";
        return status;
      }

      function setBusy(row, busy) {
        row.querySelectorAll(".quick-actions button").forEach((button) => {
          button.disabled = busy;
          button.style.opacity = busy ? "0.55" : "";
        });
      }

      function showNotice(message) {
        const notice = document.querySelector("[data-live-notice]");
        if (!notice) return;
        notice.textContent = message;
        notice.classList.add("is-visible");
        window.clearTimeout(showNotice.timer);
        showNotice.timer = window.setTimeout(() => {
          notice.classList.remove("is-visible");
        }, 2600);
      }

      function updateCounts(counts) {
        if (!counts) return;
        document.querySelectorAll("[data-stat]").forEach((card) => {
          const label = card.getAttribute("data-stat");
          const value = counts[label];
          if (value !== undefined) {
            const target = card.querySelector("strong");
            if (target) target.textContent = value;
          }
        });
      }

      function updateRow(row, status) {
        const current = currentListStatus();
        if (current !== "all" && current !== status) {
          row.remove();
          showNotice(`已标记为${status}，记录已移出当前筛选列表`);
          return;
        }
        const badge = row.querySelector("[data-status-badge]");
        if (!badge) return;
        badge.textContent = status;
        badge.className = `status ${statusClasses[status] || ""}`;
        row.querySelectorAll("[data-target-status]").forEach((button) => {
          button.classList.toggle("is-current", button.getAttribute("data-target-status") === status);
          button.setAttribute(
            "aria-pressed",
            button.getAttribute("data-target-status") === status ? "true" : "false"
          );
        });
        row.classList.remove("row-flash");
        void row.offsetWidth;
        row.classList.add("row-flash");
        showNotice(`已更新为${status}`);
      }

      document.addEventListener("search", (event) => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || input.name !== "q" || input.value) return;
        const form = input.form;
        if (form) form.requestSubmit();
      });

      document.addEventListener("submit", async (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.closest(".quick-actions")) return;
        event.preventDefault();

        const row = form.closest("[data-site-row]");
        if (!row) {
          form.submit();
          return;
        }

        setBusy(row, true);
        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {
              "Accept": "application/json",
              "X-Requested-With": "fetch",
            },
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          updateCounts(data.counts);
          updateRow(row, data.status);
        } catch (error) {
          form.submit();
        } finally {
          if (row.isConnected) setBusy(row, false);
        }
      });
    })();
  </script>
    """


def status_form(
    site_id: int,
    status: str,
    label: str,
    return_to: str,
    button_class: str,
    current_status: str | None = None,
) -> str:
    is_current = current_status == status
    classes = f"{button_class}{' is-current' if is_current else ''}"
    pressed = "true" if is_current else "false"
    return f"""
    <form method="post" action="/sites/{site_id}/status">
      <input type="hidden" name="status" value="{escape(status)}">
      <input type="hidden" name="return_to" value="{escape(return_to)}">
      <button class="{escape(classes)}" type="submit" data-target-status="{escape(status)}" aria-pressed="{pressed}">{escape(label)}</button>
    </form>
    """


def status_badge(status: str) -> str:
    class_name = {
        SiteStatus.TODO.value: "todo",
        SiteStatus.IN_PROGRESS.value: "doing",
        SiteStatus.DONE.value: "done",
        SiteStatus.PAUSED.value: "paused",
        SiteStatus.NO_ACTION.value: "none",
    }.get(status, "")
    return f'<span class="status {class_name}" data-status-badge>{escape(status)}</span>'


def site_link(site: SiteRecord) -> str:
    url = site.canonical_url or f"https://{site.domain}"
    safe_url = escape(url)
    return f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_url}</a>'


def fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def selected_status_from_query(value: str | None) -> str | None:
    if value is None or not value.strip():
        return SiteStatus.TODO.value
    if value.strip().lower() in {"all", "全部"}:
        return None
    return normalize_status(value)


def counts_with_total(counts: dict[str, int]) -> dict[str, int]:
    return {"全部": sum(counts.values()), **counts}


def is_fetch_request(request: Request) -> bool:
    return request.headers.get("x-requested-with", "").lower() == "fetch"


def current_path(request: Request) -> str:
    params = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
    if not params:
        return request.url.path
    return f"{request.url.path}?{urlencode(params)}"


def safe_return_to(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    if not value.startswith("/") or value.startswith("//"):
        return fallback
    return value
