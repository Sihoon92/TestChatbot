import aiosqlite

# 세션 메타데이터 테이블. 실제 대화 메시지는 LangGraph 체크포인터가 별도 테이블에
# 저장하므로 여기서는 목록/제목/정렬용 메타데이터만 관리한다. 필요에 따라 컬럼/테이블을
# 확장하라(예: users, messages 감사 로그 등). SQLite 스키마의 기본 토대다.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute(_SCHEMA)
        await conn.commit()
