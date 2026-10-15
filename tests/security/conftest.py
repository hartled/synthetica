"""Local, sandboxed security regression tests. Never point these at production.

App is imported with a throwaway SQLite DB in a temp dir and exercised only via
FastAPI's in-process TestClient (no network)."""
import os, sys, tempfile, uuid, pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TMP = tempfile.mkdtemp(prefix="synth-sec-test-")
os.environ.update(DATABASE_PATH=os.path.join(_TMP, "test.db"), SECRET_KEY="test-secret",
                  ADMIN_KEY="test-admin-key", ADMIN_KEY_D="test-admin-d",
                  ADMIN_KEY_CEO="test-admin-ceo", TICK_SECONDS="3600", COOKIE_SECURE="false")
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Named actor d. The legacy singleton "test-admin-key" must not authenticate.
ADMIN = {"Authorization": "Bearer test-admin-d"}

@pytest.fixture(scope="session")
def client():
    with TestClient(appmod.app, base_url="http://testserver") as c:
        yield c

@pytest.fixture(autouse=True)
def _reset_rate_limits():
    appmod._hits.clear(); yield; appmod._hits.clear()

def uniq(p="t"): return f"{p}{uuid.uuid4().hex[:10]}"

@pytest.fixture
def new_bot(client):
    def make(**kw):
        body = dict(name="Tester", handle=uniq("b"), persona="", avatar="🤖", category="other"); body.update(kw)
        r = client.post("/api/bots", json=body); assert r.status_code == 201, r.text
        d = r.json(); return d["handle"], {"Authorization": f"Bearer {d['api_key']}"}
    return make

@pytest.fixture
def new_human():
    def make():
        c = TestClient(appmod.app, base_url="http://testserver")
        u = uniq("h"); r = c.post("/api/auth/signup", json=dict(email=f"{u}@example.com", username=u, password="password123"))
        assert r.status_code == 201, r.text
        return u, c
    return make
