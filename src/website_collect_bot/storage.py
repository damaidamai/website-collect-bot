from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from website_collect_bot.models import SiteRecord, SiteStatus


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
                """
            )
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

    async def get_site(self, domain: str) -> SiteRecord | None:
        async with aiosqlite.connect(self.database_path) as db:
            row = await self._get_site_row(db, domain)
            return row_to_site(row) if row else None

    async def list_sites(self, status: str | None = None, limit: int = 20) -> list[SiteRecord]:
        query = """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at
            FROM sites
        """
        params: tuple[object, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params = (*params, limit)

        async with aiosqlite.connect(self.database_path) as db:
            rows = await db.execute_fetchall(query, params)
            return [row_to_site(row) for row in rows]

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

    async def _get_site_row(self, db: aiosqlite.Connection, domain: str) -> aiosqlite.Row | None:
        cursor = await db.execute(
            """
            SELECT id, domain, canonical_url, title, status, summary, notes, first_seen_at, updated_at
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


def row_to_site(row: aiosqlite.Row) -> SiteRecord:
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
    )
