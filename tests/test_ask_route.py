import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_collection(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()
    app = create_app(test_config, "s")
    return app, cid


@pytest.mark.asyncio
async def test_collection_has_ask_form(app_with_collection):
    app, cid = app_with_collection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/collections/{cid}")
        assert 'action="/sessions"' in r.text
        assert f'value="{cid}"' in r.text