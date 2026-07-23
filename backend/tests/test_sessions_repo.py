import os
import tempfile

import pytest

from app.db.database import init_db
from app.db import sessions_repo as repo


@pytest.fixture()
async def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    for p in (path, f"{path}-wal", f"{path}-shm"):
        try:
            os.remove(p)
        except OSError:
            pass


async def test_create_and_get(db_path):
    created = await repo.create_session(db_path, title="hello")
    assert created["title"] == "hello"
    fetched = await repo.get_session(db_path, created["id"])
    assert fetched == created


async def test_list_orders_by_updated_desc(db_path):
    a = await repo.create_session(db_path, title="a")
    b = await repo.create_session(db_path, title="b")
    await repo.touch_session(db_path, a["id"])  # a becomes most recent
    ids = [s["id"] for s in await repo.list_sessions(db_path)]
    assert ids[0] == a["id"] and b["id"] in ids


async def test_rename(db_path):
    s = await repo.create_session(db_path, title="old")
    updated = await repo.rename_session(db_path, s["id"], "new")
    assert updated["title"] == "new"


async def test_delete(db_path):
    s = await repo.create_session(db_path, title="x")
    assert await repo.delete_session(db_path, s["id"]) is True
    assert await repo.get_session(db_path, s["id"]) is None
    assert await repo.delete_session(db_path, s["id"]) is False
