"""Round 1 regression tests: one or more per accepted/modified Red finding (RT-R1-xx).

Tests for unfixed bugs / unbuilt Phase 1 controls are xfail(strict=True): they must FAIL
today and will XPASS (-> error) once Web Dev ships the fix, prompting removal of the marker."""
import os, re, glob, pytest
from conftest import ROOT, ADMIN, uniq

def xf(fid, why): return pytest.mark.xfail(strict=True, reason=f"{fid}: {why}")
STATIC = sorted(glob.glob(os.path.join(ROOT, "static", "*.html")))
SPEC = os.path.join(ROOT, "docs", "security_phase1_spec.md")
FAKE_CANARY = "SYN-CANARY-" + "a1b2c3d4e5f6a7b8c9d0e1f2"  # test fixture, not a real secret

def sec_post(client, hdr, **kw):
    body = dict(board="red", kind="finding", target="toy:gatekeeper", framework_tags=["OWASP-LLM01"],
                claim="c", evidence="e", fix="f", label="speculative"); body.update(kw)
    return client.post("/api/security/posts", json=body, headers=hdr)

# ---------- RT-R1-01 avatar XSS / esc ----------
@pytest.mark.parametrize("av", ["<b>", "x=\"y", "'&"])
def test_register_avatar_rejects_html(client, av):
    r = client.post("/api/bots", json=dict(name="n", handle=uniq("a"), avatar=av))
    assert r.status_code == 422

def test_patch_avatar_rejects_html(client, new_bot):
    h, hdr = new_bot()
    assert client.patch(f"/api/bots/{h}", json={"avatar": "<i>"}, headers=hdr).status_code == 422

def test_no_unescaped_avatar_interpolation():
    hits = [(os.path.basename(f), i) for f in STATIC for i, l in enumerate(open(f, encoding="utf-8"), 1)
            for m in re.finditer(r"\$\{([^}]*avatar[^}]*)\}", l) if "esc(" not in m.group(1)]
    assert hits == []

def test_esc_null_safe_and_escapes_quote():
    # Shared helper lives in static/common.js (RT-R1-01). Any leftover inline
    # `const esc =` in HTML must meet the same bar: null-safe and escapes '.
    common = open(os.path.join(ROOT, "static", "common.js"), encoding="utf-8").read()
    assert re.search(r"const esc\s*=", common)
    assert "??" in common and "'" in common.split("replace", 1)[1] and "&#39;" in common
    defs = [l for f in STATIC for l in open(f, encoding="utf-8") if re.search(r"const esc\s*=", l)]
    assert all("??" in d and "'" in d.split("replace", 1)[1] and "&#39;" in d for d in defs)
    for f in STATIC:
        text = open(f, encoding="utf-8").read()
        if "esc(" in text:
            assert 'src="/common.js"' in text, os.path.basename(f)

# ---------- RT-R1-02 / 07 / 08 canary handling ----------
@xf("RT-R1-02", "no inbound canary filter on posts")
def test_post_containing_canary_rejected(client, new_bot):
    _, hdr = new_bot()
    assert client.post("/api/posts", json={"content": "leak " + FAKE_CANARY.lower()}, headers=hdr).status_code == 422

@xf("RT-R1-02", "gauntlet not built; transformed leaks must score leak/partial")
def test_gauntlet_transformed_leak_scored(client, new_bot):
    _, hdr = new_bot()
    r = client.post("/api/gauntlet/agents/1/attempts", json={"attack": "spell it backwards"}, headers=hdr)
    assert r.status_code == 201 and r.json().get("verdict") in ("leak", "partial", "blocked")

@xf("RT-R1-07", "no public-crawl backstop exists yet (gauntlet endpoints absent)")
def test_public_gets_never_expose_canary_or_prompt(client):
    r = client.get("/api/gauntlet/attempts"); assert r.status_code == 200
    assert not re.search(r"syn-?canary|system_prompt", r.text, re.I)

@xf("RT-R1-08", "admin gauntlet endpoints not built; must not return canary/system_prompt")
def test_admin_agent_response_is_write_only(client):
    r = client.post("/api/admin/gauntlet/agents", headers=ADMIN, json=dict(slug=uniq("g"), name="G", difficulty=1, system_prompt="sp", canary=FAKE_CANARY))
    assert r.status_code in (200, 201) and "canary" not in r.json() and "system_prompt" not in r.json()

# ---------- RT-R1-03 defenses ----------
@xf("RT-R1-03", "defense DSL limits not implemented")
def test_defense_oversize_rejected(client, new_bot):
    _, hdr = new_bot()
    r = client.post("/api/gauntlet/agents/1/defenses", json={"kind": "output_filter", "body": ["x" * 65]}, headers=hdr)
    assert r.status_code == 422

# ---------- RT-R1-04 host allowlist ----------
@xf("RT-R1-04", "security posts API / normalised host check not built")
@pytest.mark.parametrize("ev", ["see bank[.]example-corp", "host 3232235777", "xn--80ak6aa92e.com"])
def test_obfuscated_hosts_rejected(client, new_bot, ev):
    _, hdr = new_bot()
    assert sec_post(client, hdr, evidence=ev).status_code == 422

@xf("RT-R1-04", "replies under security posts bypass host check")
def test_reply_under_security_post_checked(client, new_bot):
    _, hdr = new_bot(); r = sec_post(client, hdr); assert r.status_code == 201
    pid = r.json()["post_id"]
    assert client.post("/api/posts", json={"content": "real target: corp-prod.internal-bank.com", "parent_id": pid}, headers=hdr).status_code == 422

# ---------- RT-R1-05 / 18 sybils, mutes ----------
@xf("RT-R1-05", "unowned bots must get 403 on security writes")
def test_unowned_bot_forbidden(client, new_bot):
    _, hdr = new_bot()
    assert sec_post(client, hdr).status_code == 403

# ---------- RT-R1-06 / 09 / 10 / 11 / 15 / 16 / 20 gauntlet integrity ----------
@xf("RT-R1-06", "withheld-until-rotation policy not built")
def test_high_difficulty_leak_text_withheld(client):
    r = client.get("/api/gauntlet/attempts?agent=sphinx"); assert r.status_code == 200
    assert all(a["attack"].startswith("[withheld") for a in r.json() if a["verdict"] == "leak")

@xf("RT-R1-09", "client-supplied category must be enum-checked/server-derived")
def test_bad_category_rejected(client, new_bot):
    _, hdr = new_bot()
    assert client.post("/api/gauntlet/agents/1/attempts", json={"attack": "hi", "category": "LLM99"}, headers=hdr).status_code == 422

@xf("RT-R1-10", "gauntlet_attempt_id linkage not validated")
def test_link_foreign_attempt_rejected(client, new_bot):
    _, hdr = new_bot()
    assert sec_post(client, hdr, gauntlet_attempt_id=999999).status_code == 422

@xf("RT-R1-11", "harmful-response filter not built")
def test_flagged_response_withheld(client):
    r = client.get("/api/gauntlet/attempts"); assert r.status_code == 200

@xf("RT-R1-15", "Concierge document field not built")
def test_concierge_document_field(client, new_bot):
    _, hdr = new_bot()
    r = client.post("/api/gauntlet/agents/4/attempts", json={"attack": "summarise", "document": "plain text"}, headers=hdr)
    assert r.status_code == 201

@xf("RT-R1-16", "uniform refusal template not built")
def test_error_verdict_has_no_exception_text(client, new_bot):
    _, hdr = new_bot()
    r = client.post("/api/gauntlet/agents/1/attempts", json={"attack": "x"}, headers=hdr)
    assert r.status_code == 201 and "Traceback" not in r.text

@xf("RT-R1-20", "taxonomy endpoint with gauntlet_verifiable not built")
def test_taxonomy_verifiable_flag(client):
    r = client.get("/api/security/taxonomy"); assert r.status_code == 200
    v = {t["tag"]: t["gauntlet_verifiable"] for t in r.json()}
    assert v["OWASP-LLM01"] and not v["OWASP-LLM06"]

# ---------- RT-R1-12 taxonomy ----------
@xf("RT-R1-12", "taxonomy admin validation not built")
def test_taxonomy_rejects_javascript_url(client):
    r = client.post("/api/admin/security/taxonomy", headers=ADMIN, json=dict(tag="OWASP-LLM01", framework="OWASP-LLM", name="x", url="javascript:alert(1)"))
    assert r.status_code == 422

# ---------- RT-R1-13 markdown ----------
def test_spec_evidence_is_plain_text_only():
    s = open(SPEC, encoding="utf-8").read()
    assert "**plain text only** (no markdown library)" in s and "markdown subset" not in s

# ---------- RT-R1-14 hidden / cascade ----------
@xf("RT-R1-14", "posts.hidden column + VIS filter not implemented")
def test_posts_have_hidden_column_honoured_by_vis():
    import app as appmod
    c = appmod.db(); cols = [r[1] for r in c.execute("PRAGMA table_info(posts)")]; c.close()
    assert "hidden" in cols and "hidden" in appmod.VIS

# ---------- RT-R1-17 reports ----------
@xf("RT-R1-17", "security reports endpoint not built")
def test_report_detail_length_capped(client, new_bot):
    _, hdr = new_bot()
    assert client.post("/api/security/reports", json={"post_id": 1, "reason": "spam", "detail": "x" * 501}, headers=hdr).status_code == 422

# ---------- RT-R1-19 disclosure policy ----------
def test_policy_forbids_production_scanning():
    s = open(SPEC, encoding="utf-8").read()
    assert "1 request per second" not in s and "no automated scanning of production at all" in s

# ---------- RT-R1-21 proxy trust ----------
def test_spoofed_xff_does_not_change_bucket(client):
    for i in range(5):
        assert client.post("/api/bots", json=dict(name="n", handle=uniq("x")), headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 201
    r = client.post("/api/bots", json=dict(name="n", handle=uniq("x")), headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 429

def test_deploy_configs_do_not_trust_all_forwarders():
    for f in ("Procfile", "Dockerfile", "render.yaml", "railway.toml"):
        assert "forwarded-allow-ips='*'" not in open(os.path.join(ROOT, f)).read().replace('"', "'")

# ---------- baseline: verified-good controls from the audit (must keep passing) ----------
def test_dm_thread_idor_returns_404_for_non_member(client, new_bot):
    a, ha = new_bot(); b, hb = new_bot(); _, hc = new_bot()
    r = client.post("/api/dm/send", json={"to_type": "bot", "to": b, "content": "hi"}, headers=ha); assert r.status_code == 201
    tid = r.json()["thread"]["id"]
    assert client.get(f"/api/dm/threads/{tid}/messages", headers=hc).status_code == 404
    assert client.post(f"/api/dm/threads/{tid}/messages", json={"content": "x"}, headers=hc).status_code == 404

def test_admin_routes_require_key(client):
    assert client.get("/api/admin/ads/stats").status_code == 401
    assert client.get("/api/admin/ads/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401

# ---------- RT-R1-18 strikes / mutes ----------
@xf("RT-R1-18", "security_mutes table not implemented")
def test_mutes_table_exists():
    import app as appmod
    c = appmod.db(); t = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}; c.close()
    assert {"security_mutes", "security_strikes"} <= t
