"""Per-admin credentials: slugs d and ceo, hashed keys, revoke and rotate.

Defense merge-approve is Phase 1. The four-eyes HTTP flow is xfail(strict=True)
until that API exists. enforce_four_eyes is the check it must call.
"""
import json
import time

import pytest

import app as appmod

KEY_D = "test-admin-d"
KEY_CEO = "test-admin-ceo"
HDR_D = {"Authorization": f"Bearer {KEY_D}"}
HDR_CEO = {"Authorization": f"Bearer {KEY_CEO}"}
PUBLIC_FIELDS = {"slug", "display_name", "active", "created", "revoked"}


def _install_known():
    appmod.bootstrap_named_admin_keys(KEY_D, KEY_CEO)


@pytest.fixture(autouse=True)
def _known_admins():
    _install_known()
    yield
    _install_known()


def _last(action):
    c = appmod.db()
    row = c.execute(
        "SELECT actor_slug, action, target, detail FROM security_audit_log WHERE action=? ORDER BY id DESC LIMIT 1",
        (action,),
    ).fetchone()
    c.close()
    return row


def test_each_bearer_authenticates_as_its_slug(client):
    listed = client.get("/api/admin/actors", headers=HDR_D)
    assert listed.status_code == 200, listed.text
    assert _last("auth_ok")["actor_slug"] == "d"
    assert client.get("/api/admin/actors", headers=HDR_CEO).status_code == 200
    assert _last("auth_ok")["actor_slug"] == "ceo"
    c = appmod.db()
    rows = c.execute("SELECT slug, display_name, key_hash, active FROM admin_actors").fetchall()
    c.close()
    by_slug = {r["slug"]: r for r in rows}
    assert set(by_slug) >= {"d", "ceo"}
    assert by_slug["d"]["display_name"] == "D" and by_slug["d"]["active"] == 1
    assert by_slug["ceo"]["display_name"] == "Synthetica CEO" and by_slug["ceo"]["active"] == 1
    assert by_slug["d"]["key_hash"] == appmod.hkey(KEY_D)
    assert by_slug["ceo"]["key_hash"] == appmod.hkey(KEY_CEO)
    assert KEY_D not in by_slug["d"]["key_hash"] and KEY_CEO not in by_slug["ceo"]["key_hash"]


def test_revoked_key_is_rejected(client):
    revoked = client.post("/api/admin/actors/ceo/revoke", headers=HDR_D)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["active"] is False
    assert client.get("/api/admin/actors", headers=HDR_CEO).status_code == 401
    assert client.post("/api/admin/bots/chefbyte/ban", headers=HDR_CEO).status_code == 401
    assert client.get("/api/admin/ads/stats", headers=HDR_CEO).status_code == 401
    assert client.get("/api/admin/actors", headers=HDR_D).status_code == 200
    assert _last("revoke")["actor_slug"] == "d"
    assert _last("revoke")["target"] == "ceo"


def test_rotated_key_replaces_old_and_audits_the_other_actor(client):
    rotated = client.post("/api/admin/actors/ceo/rotate", headers=HDR_D)
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    new_key = body["api_key"]
    assert new_key.startswith("syn_adm_")
    assert set(body) == {"slug", "display_name", "active", "api_key"}
    assert "key_hash" not in rotated.text
    assert client.get("/api/admin/actors", headers=HDR_CEO).status_code == 401
    fresh = {"Authorization": f"Bearer {new_key}"}
    assert client.get("/api/admin/actors", headers=fresh).status_code == 200
    assert _last("auth_ok")["actor_slug"] == "ceo"
    row = _last("rotate")
    assert row["actor_slug"] == "d" and row["target"] == "ceo"
    c = appmod.db()
    stored = c.execute("SELECT key_hash FROM admin_actors WHERE slug='ceo'").fetchone()[0]
    details = " ".join((r[0] or "") for r in c.execute("SELECT detail FROM security_audit_log"))
    c.close()
    assert stored == appmod.hkey(new_key)
    assert new_key not in stored and new_key not in details and KEY_CEO not in details


def test_migrate_keeps_rotated_hash(client):
    new_key = client.post("/api/admin/actors/d/rotate", headers=HDR_CEO).json()["api_key"]
    appmod.migrate()
    assert client.get("/api/admin/actors", headers=HDR_D).status_code == 401
    assert client.get("/api/admin/actors", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200


def test_no_self_revoke_and_last_active_key_stays(client):
    assert client.post("/api/admin/actors/d/revoke", headers=HDR_D).status_code == 403
    assert client.post("/api/admin/actors/ceo/revoke", headers=HDR_D).status_code == 200
    assert client.post("/api/admin/actors/d/revoke", headers=HDR_D).status_code == 403
    assert client.get("/api/admin/actors", headers=HDR_D).status_code == 200
    assert client.get("/api/admin/actors", headers=HDR_CEO).status_code == 401


def test_legacy_singleton_does_not_authenticate(client):
    assert client.get("/api/admin/actors", headers={"Authorization": "Bearer test-admin-key"}).status_code == 401


def test_ban_audits_credential_slug_not_request_body(client, new_bot):
    handle, _hdr = new_bot()
    banned = client.post(
        f"/api/admin/bots/{handle}/ban",
        headers=HDR_D,
        json={"slug": "ceo", "actor_slug": "ceo"},
    )
    assert banned.status_code == 200, banned.text
    row = _last("ban")
    assert row["actor_slug"] == "d" and row["target"] == handle
    assert "key_hash" not in banned.text and KEY_D not in banned.text
    assert client.post(f"/api/admin/bots/{handle}/unban", headers=HDR_D).status_code == 200


def test_responses_and_openapi_omit_key_material(client):
    listed = client.get("/api/admin/actors", headers=HDR_D)
    assert listed.status_code == 200
    assert "key_hash" not in listed.text
    assert KEY_D not in listed.text and KEY_CEO not in listed.text
    missing = client.get("/api/admin/actors")
    wrong = client.get("/api/admin/actors", headers={"Authorization": f"Bearer {KEY_D}-nope"})
    assert missing.status_code == 401 and wrong.status_code == 401
    assert KEY_D not in missing.text and KEY_D not in wrong.text
    assert "key_hash" not in missing.text and "key_hash" not in wrong.text
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "key_hash" not in spec.text
    assert KEY_D not in spec.text and KEY_CEO not in spec.text
    examples = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("example", "examples"):
                    examples.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec.json())
    flat = json.dumps(examples)
    assert "key_hash" not in flat and "syn_adm_" not in flat
    assert KEY_D not in flat and KEY_CEO not in flat


def test_actor_list_never_includes_key_hash(client):
    body = client.get("/api/admin/actors", headers=HDR_CEO).json()
    slugs = {row["slug"] for row in body}
    assert {"d", "ceo"} <= slugs
    for row in body:
        assert set(row) == PUBLIC_FIELDS
        assert "key_hash" not in row


def test_zero_active_actors_fail_closed(client, monkeypatch):
    monkeypatch.delenv("ADMIN_KEY_D", raising=False)
    monkeypatch.delenv("ADMIN_KEY_CEO", raising=False)
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    c = appmod.db()
    c.execute("UPDATE admin_actors SET active=0, revoked=?", (time.time(),))
    c.commit()
    c.close()
    headers = (None, HDR_D, HDR_CEO, {"Authorization": "Bearer test-admin-key"})
    for hdr in headers:
        kwargs = {} if hdr is None else {"headers": hdr}
        assert client.get("/api/admin/actors", **kwargs).status_code == 503
        assert client.post("/api/admin/actors/ceo/revoke", **kwargs).status_code == 503
        assert client.post("/api/admin/bots/chefbyte/ban", **kwargs).status_code == 503


def test_four_eyes_helper_rejects_submitter():
    with pytest.raises(appmod.HTTPException) as caught:
        appmod.enforce_four_eyes({"id": 1, "slug": "d", "display_name": "D"}, "d")
    assert caught.value.status_code == 403
    appmod.enforce_four_eyes({"id": 2, "slug": "ceo", "display_name": "Synthetica CEO"}, "d")
    appmod.enforce_four_eyes({"id": 1, "slug": "d", "display_name": "D"}, None)


@pytest.mark.xfail(strict=True, reason="Phase 1: defense merge-approve is not built; four-eyes (R2-A14′) applies once submit stores the admin slug")
def test_defense_submit_and_merge_approve_four_eyes(client):
    submitted = client.post(
        "/api/admin/defenses",
        headers=HDR_D,
        json={"kind": "output_filter", "body": "block the canary"},
    )
    assert submitted.status_code in (200, 201), submitted.text
    defense_id = submitted.json()["id"]
    same = client.post(f"/api/admin/defenses/{defense_id}/merge-approve", headers=HDR_D)
    assert same.status_code == 403
    other = client.post(f"/api/admin/defenses/{defense_id}/merge-approve", headers=HDR_CEO)
    assert other.status_code == 200
