import uuid
from datetime import datetime, timezone

import aiosqlite

_COLS = "id, title, created_at, updated_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: tuple) -> dict:
    return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3]}


async def create_session(db_path: str, title: str) -> dict:
    sid = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            f"INSERT INTO sessions ({_COLS}) VALUES (?, ?, ?, ?)",
            (sid, title, now, now),
        )
        await conn.commit()
    return {"id": sid, "title": title, "created_at": now, "updated_at": now}


async def list_sessions(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM sessions ORDER BY updated_at DESC"
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_session(db_path: str, session_id: str) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def rename_session(db_path: str, session_id: str, title: str) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await conn.commit()
    return await get_session(db_path, session_id)


async def touch_session(db_path: str, session_id: str) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
        )
        await conn.commit()


async def delete_session(db_path: str, session_id: str) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await conn.commit()
        return cur.rowcount > 0
