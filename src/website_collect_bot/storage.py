from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from website_collect_bot.extract import canonical_site_key
from website_collect_bot.models import ScanRun, ScanStatus, SiteRecord, SiteStatus


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL UNIQUE,
                    canonical_url TEXT,
                    title TEXT,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    sender_name TEXT,
                    message_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS site_messages (
                    site_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (site_id, message_id),
                    FOREIGN KEY (site_id) REFERENCES sites(id),
                    FOREIGN KEY (message_id) REFERENCES messages(id)
                );

                CREATE TABLE IF NOT EXISTS site_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                );

                CREATE TABLE IF NOT EXISTS status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL,
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                );

                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '',
                    raw_path TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                );
                """
            )
            await self._ensure_scan_columns(db)
            await self._merge_subdomain_sites(db)
            await db.commit()

    async def record_message(
        self,
        telegram_message_id: int,
        chat_id: int,
        sender_name: str | None,
        message_text: str,
    ) -> int:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO messages (telegram_message_id, chat_id, sender_name, message_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (telegram_message_id, chat_id, sender_name, message_text, utc_now_iso()),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def upsert_site(
        self,
        domain: str,
        canonical_url: str | None,
        title: str | None,
        summary: str,
        notes: str | None,
        status: str | None = None,
    ) -> SiteRecord:
        now = utc_now_iso()
        desired_status = status or SiteStatus.TODO.value
        async with aiosqlite.connect(self.database_path) as db:
            existing = await self._get_site_row(db, domain)
            if existing is None:
                await db.execute(
                    """
                    INSERT INTO sites
                        (domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        domain,
                        canonical_url,
                        title,
                        desired_status,
                        summary,
                        notes or "",
                        now,
                        now,
                    ),
                )
            else:
                site_id, old_status = int(existing[0]), str(existing[4])
                new_status = status or old_status
                await db.execute(
                    """
                    UPDATE sites
                    SET canonical_url = COALESCE(?, canonical_url),
                        title = COALESCE(?, title),
                        status = ?,
                        summary = CASE WHEN ? != '' THEN ? ELSE summary END,
                        notes = CASE WHEN ? != '' THEN ? ELSE notes END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        canonical_url,
                        title,
                        new_status,
                        summary,
                        summary,
                        notes or "",
                        notes or "",
                        now,
                        site_id,
                    ),
                )
                if new_status != old_status:
                    await self._insert_status_history(db, site_id, old_status, new_status, "自动识别")
            await db.commit()
            row = await self._get_site_row(db, domain)
            if row is None:
                raise RuntimeError(f"site upsert failed: {domain}")
            return row_to_site(row)

    async def link_message_to_site(self, message_id: int, site_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO site_messages (site_id, message_id) VALUES (?, ?)",
                (site_id, message_id),
            )
            await db.commit()

    async def add_event(self, site_id: int, event_type: str, content: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                "INSERT INTO site_events (site_id, event_type, content, created_at) VALUES (?, ?, ?, ?)",
                (site_id, event_type, content, utc_now_iso()),
            )
            await db.commit()

    async def set_status(
        self,
        domain: str,
        new_status: str,
        reason: str | None = None,
        notes: str | None = None,
    ) -> SiteRecord | None:
        now = utc_now_iso()
        async with aiosqlite.connect(self.database_path) as db:
            row = await self._get_site_row(db, domain)
            if row is None:
                return None
            site = row_to_site(row)
            
            updated_notes = site.notes
            if notes:
                if updated_notes:
                    updated_notes = f"{updated_notes}\n{notes}"
                else:
                    updated_notes = notes

            await db.execute(
                "UPDATE sites SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
                (new_status, updated_notes, now, site.id),
            )
            if site.status != new_status:
                await self._insert_status_history(db, site.id, site.status, new_status, reason)
            await db.commit()
            updated = await self._get_site_row(db, domain)
            return row_to_site(updated) if updated else None

    async def update_site_by_id(
        self,
        site_id: int,
        status: str | None = None,
        summary: str | None = None,
        notes: str | None = None,
        reason: str | None = None,
    ) -> SiteRecord | None:
        now = utc_now_iso()
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
                FROM sites
                WHERE id = ?
                """,
                (site_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            site = row_to_site(row)
            next_status = status if status is not None else site.status
            next_summary = summary if summary is not None else site.summary
            next_notes = notes if notes is not None else site.notes

            await db.execute(
                """
                UPDATE sites
                SET status = ?, summary = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_status, next_summary, next_notes, now, site.id),
            )
            if next_status != site.status:
                await self._insert_status_history(db, site.id, site.status, next_status, reason)
            if next_summary != site.summary or next_notes != site.notes:
                await db.execute(
                    """
                    INSERT INTO site_events (site_id, event_type, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (site.id, "web_update", reason or "Web 面板更新摘要/备注", now),
                )
            await db.commit()
            updated = await self.get_site_by_id(site.id)
            return updated

    async def get_site(self, domain: str) -> SiteRecord | None:
        async with aiosqlite.connect(self.database_path) as db:
            row = await self._get_site_row(db, domain)
            return row_to_site(row) if row else None

    async def list_sites(self, status: str | None = None, limit: int = 20) -> list[SiteRecord]:
        query = """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
            FROM sites
        """
        params: tuple[object, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at ASC LIMIT ?"
        params = (*params, limit)

        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(query, params)
            return [row_to_site(row) for row in rows]

    async def status_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                "SELECT status, COUNT(*) FROM sites GROUP BY status ORDER BY status"
            )
            return {str(row[0]): int(row[1]) for row in rows}

    async def search_sites(
        self,
        status: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[SiteRecord]:
        sql = """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
            FROM sites
        """
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            like = f"%{query.strip()}%"
            clauses.append(
                """
                (
                    domain LIKE ?
                    OR canonical_url LIKE ?
                    OR title LIKE ?
                    OR summary LIKE ?
                    OR notes LIKE ?
                )
                """
            )
            params.extend([like, like, like, like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at ASC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(sql, tuple(params))
            return [row_to_site(row) for row in rows]

    async def get_site_by_id(self, site_id: int) -> SiteRecord | None:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
                FROM sites
                WHERE id = ?
                """,
                (site_id,),
            )
            row = await cursor.fetchone()
            return row_to_site(row) if row else None

    async def site_messages(self, site_id: int, limit: int = 50) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT m.telegram_message_id, m.sender_name, m.message_text, m.created_at
                FROM messages m
                JOIN site_messages sm ON sm.message_id = m.id
                WHERE sm.site_id = ?
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (site_id, limit),
            )
            return [
                {
                    "telegram_message_id": int(row[0]),
                    "sender_name": row[1] or "",
                    "message_text": str(row[2] or ""),
                    "created_at": str(row[3]),
                }
                for row in rows
            ]

    async def site_events(self, site_id: int, limit: int = 50) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT event_type, content, created_at
                FROM site_events
                WHERE site_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (site_id, limit),
            )
            return [
                {"event_type": str(row[0]), "content": str(row[1] or ""), "created_at": str(row[2])}
                for row in rows
            ]

    async def status_history(self, site_id: int, limit: int = 50) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT old_status, new_status, reason, created_at
                FROM status_history
                WHERE site_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (site_id, limit),
            )
            return [
                {
                    "old_status": str(row[0] or ""),
                    "new_status": str(row[1]),
                    "reason": str(row[2] or ""),
                    "created_at": str(row[3]),
                }
                for row in rows
            ]

    async def recent_site_messages(self, site_id: int, limit: int = 8) -> list[str]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT m.message_text
                FROM messages m
                JOIN site_messages sm ON sm.message_id = m.id
                WHERE sm.site_id = ?
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (site_id, limit),
            )
            return [str(row[0]) for row in rows]
    async def list_sites_for_scan(
        self,
        scan_status: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SiteRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if scan_status:
            clauses.append("scan_status = ?")
            params.append(scan_status)
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
            FROM sites
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at ASC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(sql, tuple(params))
            return [row_to_site(row) for row in rows]

    async def get_scan_runs(self, site_id: int, limit: int = 50) -> list[ScanRun]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, site_id, tool, status, started_at, finished_at,
                       result_json, raw_path, summary
                FROM scan_runs
                WHERE site_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (site_id, limit),
            )
            return [
                ScanRun(
                    id=int(row[0]),
                    site_id=int(row[1]),
                    tool=str(row[2]),
                    status=str(row[3]),
                    started_at=row[4],
                    finished_at=row[5],
                    result_json=str(row[6] or ""),
                    raw_path=row[7],
                    summary=str(row[8] or ""),
                )
                for row in rows
            ]

    async def add_scan_run(
        self,
        site_id: int,
        tool: str,
        status: str = ScanStatus.RUNNING.value,
        result_json: str = "",
        raw_path: str | None = None,
        summary: str = "",
    ) -> int:
        now = utc_now_iso()
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO scan_runs
                    (site_id, tool, status, started_at, finished_at, result_json, raw_path, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (site_id, tool, status, now, now, result_json, raw_path, summary),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def update_scan_run(
        self,
        run_id: int,
        status: str,
        result_json: str | None = None,
        raw_path: str | None = None,
        summary: str | None = None,
    ) -> bool:
        now = utc_now_iso()
        sets = ["status = ?", "finished_at = ?"]
        params: list[object] = [status, now]
        if result_json is not None:
            sets.append("result_json = ?")
            params.append(result_json)
        if raw_path is not None:
            sets.append("raw_path = ?")
            params.append(raw_path)
        if summary is not None:
            sets.append("summary = ?")
            params.append(summary)
        params.append(run_id)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                f"UPDATE scan_runs SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            await db.commit()
            return True

    async def set_scan_result(
        self,
        site_id: int,
        scan_status: str,
        scan_summary: str | None = None,
    ) -> SiteRecord | None:
        now = utc_now_iso()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                UPDATE sites
                SET scan_status = ?,
                    scan_summary = CASE WHEN ? IS NOT NULL THEN ? ELSE scan_summary END,
                    scanned_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (scan_status, scan_summary, scan_summary, now, now, site_id),
            )
            await db.execute(
                """
                INSERT INTO site_events (site_id, event_type, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (site_id, "scan_update", f"安全检测：{scan_status}", now),
            )
            await db.commit()
        return await self.get_site_by_id(site_id)

    async def _ensure_scan_columns(self, db: aiosqlite.Connection) -> None:
        cols = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(sites)")}
        for col, decl in [
            ("scan_status", "TEXT NOT NULL DEFAULT 'none'"),
            ("scan_summary", "TEXT NOT NULL DEFAULT ''"),
            ("scanned_at", "TEXT"),
        ]:
            if col not in cols:
                await db.execute(f"ALTER TABLE sites ADD COLUMN {col} {decl}")

    async def _get_site_row(self, db: aiosqlite.Connection, domain: str) -> aiosqlite.Row | None:
        cursor = await db.execute(
            """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
            FROM sites
            WHERE domain = ?
            """,
            (domain,),
        )
        return await cursor.fetchone()

    async def _insert_status_history(
        self,
        db: aiosqlite.Connection,
        site_id: int,
        old_status: str | None,
        new_status: str,
        reason: str | None,
    ) -> None:
        await db.execute(
            """
            INSERT INTO status_history (site_id, old_status, new_status, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (site_id, old_status, new_status, reason, utc_now_iso()),
        )

    async def _merge_subdomain_sites(self, db: aiosqlite.Connection) -> None:
        rows = await db.execute_fetchall(
            """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at, scan_status, scan_summary, scanned_at
            FROM sites
            ORDER BY first_seen_at ASC
            """
        )
        for row in rows:
            site = row_to_site(row)
            site_key = canonical_site_key(site.domain)
            if site_key == site.domain:
                continue

            target_row = await self._get_site_row(db, site_key)
            if target_row is None:
                await db.execute("UPDATE sites SET domain = ? WHERE id = ?", (site_key, site.id))
                continue

            target = row_to_site(target_row)
            merged_status = merge_status(target.status, site.status)
            merged_notes = merge_text(
                target.notes,
                f"来自 {site.domain}：{site.notes or site.summary}".strip(),
            )
            merged_summary = target.summary or site.summary
            await db.execute(
                """
                UPDATE sites
                SET canonical_url = COALESCE(canonical_url, ?),
                    title = COALESCE(title, ?),
                    status = ?,
                    summary = ?,
                    notes = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    site.canonical_url,
                    site.title,
                    merged_status,
                    merged_summary,
                    merged_notes,
                    max(target.updated_at, site.updated_at).isoformat(),
                    target.id,
                ),
            )
            await db.execute(
                "UPDATE OR IGNORE site_messages SET site_id = ? WHERE site_id = ?",
                (target.id, site.id),
            )
            await db.execute("DELETE FROM site_messages WHERE site_id = ?", (site.id,))
            await db.execute("UPDATE site_events SET site_id = ? WHERE site_id = ?", (target.id, site.id))
            await db.execute("UPDATE status_history SET site_id = ? WHERE site_id = ?", (target.id, site.id))
            await db.execute("DELETE FROM sites WHERE id = ?", (site.id,))


def merge_status(left: str, right: str) -> str:
    priority = {
        SiteStatus.TODO.value: 5,
        SiteStatus.IN_PROGRESS.value: 4,
        SiteStatus.PAUSED.value: 3,
        SiteStatus.DONE.value: 2,
        SiteStatus.NO_ACTION.value: 1,
    }
    return left if priority.get(left, 0) >= priority.get(right, 0) else right


def merge_text(left: str, right: str) -> str:
    right = right.strip()
    if not right:
        return left
    if not left:
        return right
    if right in left:
        return left
    return f"{left}\n{right}"


def row_to_site(row: aiosqlite.Row) -> SiteRecord:
    scanned_raw = row[11] if len(row) > 11 else None
    return SiteRecord(
        id=int(row[0]),
        domain=str(row[1]),
        canonical_url=row[2],
        title=row[3],
        status=str(row[4]),
        summary=str(row[5] or ""),
        notes=str(row[6] or ""),
        first_seen_at=parse_dt(str(row[7])),
        updated_at=parse_dt(str(row[8])),
        scan_status=str(row[9] or ScanStatus.NONE.value),
        scan_summary=str(row[10] or ""),
        scanned_at=parse_dt(scanned_raw) if scanned_raw else None,
    )
