"""Community-feed sidebar scroll and the live-post buffer.

planLivePosts is the pure decision the page uses on each poll. The scroll
restoration in static/community.html still runs; this chooses whether new posts
are inserted or held behind the button.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

from fastapi.testclient import TestClient

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node") or ("/exec-daemon/node" if os.path.isfile("/exec-daemon/node") else None)


class FeedLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        try:
            os.remove(_DB)
        except OSError:
            pass

    def test_sidebar_scrolls_on_desktop_and_stacks_on_mobile(self):
        page = self.client.get("/community")
        self.assertEqual(page.status_code, 200, page.text)
        css = page.text
        self.assertIn("position:sticky;top:76px", css)
        self.assertIn("max-height:calc(100vh - 76px)", css)
        self.assertIn("max-height:calc(100dvh - 76px)", css)
        self.assertIn("overflow-y:auto", css)
        self.assertIn("overscroll-behavior:contain", css)
        self.assertIn(
            "@media(max-width:840px){.wrap{grid-template-columns:1fr}.side{position:static;top:auto;max-height:240px;overflow:auto",
            css,
        )
        self.assertIn(".new-posts:not([hidden]){top:12px}", css)

    def test_new_posts_pill_is_a_live_button(self):
        page = self.client.get("/community").text
        self.assertIn('src="/feed-live.js"', page)
        self.assertIn('id="new-posts"', page)
        self.assertIn('class="new-posts"', page)
        self.assertIn('type="button"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('aria-atomic="true"', page)
        self.assertIn("planLivePosts", page)
        self.assertIn("applyAnchor", page)
        self.assertIn("scrollToNewPosts", page)
        self.assertNotIn("put window.scrollY back", page)
        js = self.client.get("/feed-live.js")
        self.assertEqual(js.status_code, 200, js.text)
        self.assertIn("text/javascript", js.headers["content-type"])
        self.assertIn("function planLivePosts", js.text)
        self.assertIn("1 new post", js.text)

    def test_plan_live_posts_buffers_when_scrolled_and_inserts_at_top(self):
        if not NODE:
            self.skipTest("node is not installed")
        script = r"""
const api = require("./static/feed-live.js");
const posts = [{id: 1}, {id: 2}, {id: 3}];
function check(name, cond) { if (!cond) { console.error(name); process.exit(1); } }
const held = api.planLivePosts({scrollY: 900, firstCardTop: -640, posts, shownIds: [2, 3]});
check("hold mode", held.mode === "hold" && held.jumpTop === false && held.anchor === true);
check("held ids", held.posts.map(p => p.id).join() === "2,3");
check("buffer", held.buffered.map(p => p.id).join() === "1");
const stale = api.planLivePosts({scrollY: 900, firstCardTop: -640, posts, shownIds: []});
check("stale", stale.mode === "hold-stale" && stale.posts === null && stale.buffered.length === 3);
const top = api.planLivePosts({scrollY: 0, firstCardTop: 520, posts, shownIds: [2, 3]});
check("doc top inserts", top.mode === "insert" && top.buffered.length === 0 && top.posts.length === 3 && top.anchor === true);
const caughtUp = api.planLivePosts({scrollY: 800, firstCardTop: 40, posts, shownIds: [2, 3]});
check("feed top inserts", caughtUp.mode === "insert");
const same = api.planLivePosts({scrollY: 900, firstCardTop: -400, posts, shownIds: [1, 2, 3]});
check("refresh", same.mode === "refresh" && same.buffered.length === 0);
const first = api.planLivePosts({scrollY: 900, firstCardTop: -400, firstLoad: true, posts, shownIds: []});
check("first", first.mode === "replace" && first.jumpTop === false && first.posts.length === 3);
const reveal = api.planLivePosts({scrollY: 900, reveal: true, posts, shownIds: [2]});
check("reveal", reveal.mode === "reveal" && reveal.jumpTop === true && reveal.buffered.length === 0 && reveal.anchor === false);
check("label one", api.newPostsLabel(1) === "1 new post");
check("label many", api.newPostsLabel(4) === "4 new posts");
check("near", api.isNearTop(0, -900) === true && api.isNearTop(800, -40) === false && api.isNearTop(800, 20) === true);
"""
        proc = subprocess.run(
            [NODE, "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_docs_and_admin_copy_keep_the_disk(self):
        for rel in (
            "README.md",
            "docs/stripe_runbook.md",
            "funnel.py",
            "fulfillment.py",
            "reports.py",
            "pricing.py",
        ):
            text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            self.assertIn("/data", text, rel)
            self.assertNotIn("no persistent disk", text, rel)
            self.assertNotIn("wiped on deploy", text, rel)
            self.assertNotIn("wiped on every deploy", text, rel)
            self.assertNotIn("reset on each deploy", text, rel)
            self.assertNotIn("reset on every deploy", text, rel)
