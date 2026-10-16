"""Career Card course suggestions: matching, tracked-link overrides, API, disclosure."""

import json
import os
import tempfile
import unittest
from unittest import mock

_fd, _DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB
os.environ["TICK_SECONDS"] = "100000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)
os.environ.pop("COURSE_LINKS_JSON", None)
os.environ.pop("COURSE_LINK_HOSTS", None)

from fastapi.testclient import TestClient

import app
import courses


class CourseMatchTests(unittest.TestCase):
    def test_matches_keywords_in_order_and_caps(self):
        got = courses.match_courses("Statistics, Python coding, threat thinking, quantum hardware, SQL")
        self.assertEqual([c["id"] for c in got], ["statistics", "python", "data"])
        self.assertEqual(len(courses.match_courses("statistics python security quantum", limit=2)), 2)

    def test_no_match_and_bad_input(self):
        self.assertEqual(courses.match_courses("hand and power tools, building codes"), [])
        self.assertEqual(courses.match_courses(None), [])
        self.assertEqual(courses.match_courses(""), [])

    def test_plain_urls_are_coursera_https(self):
        for _t, url, _rx in courses.COURSES.values():
            self.assertTrue(url.startswith("https://www.coursera.org/"), url)
            self.assertNotIn("?", url)

    def test_tracked_override_allowlisted_only(self):
        env = {"COURSE_LINKS_JSON": json.dumps({
            "python": "https://coursera.pxf.io/abc",
            "statistics": "https://evil.example/x",
            "nope": "https://coursera.pxf.io/zzz",
            "security": "http://coursera.pxf.io/insecure"})}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(courses.link_for("python"), "https://coursera.pxf.io/abc")
            self.assertEqual(courses.link_for("statistics"), courses.COURSES["statistics"][1])
            self.assertEqual(courses.link_for("security"), courses.COURSES["security"][1])
            got = {c["id"]: c["url"] for c in courses.match_courses("python statistics")}
            self.assertEqual(got["python"], "https://coursera.pxf.io/abc")

    def test_parser_differential_userinfo_and_lookalike_hosts_rejected(self):
        env = {"COURSE_LINKS_JSON": json.dumps({
            "python": "https://evil.com\\@www.coursera.org/phish",
            "statistics": "https://user:pass@www.coursera.org/learn/stanford-statistics",
            "ml": "https://www.coursera.org.evil.com/x",
            "data": "javascript:alert(1)",
            "security": "https://imp.i384100.net/c/1/2/14726",
            "it": "https://notcoursera.org/learn/x",
        })}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(courses.link_for("python"), courses.COURSES["python"][1])
            self.assertEqual(courses.link_for("statistics"), courses.COURSES["statistics"][1])
            self.assertEqual(courses.link_for("ml"), courses.COURSES["ml"][1])
            self.assertEqual(courses.link_for("data"), courses.COURSES["data"][1])
            self.assertEqual(courses.link_for("it"), courses.COURSES["it"][1])
            self.assertEqual(courses.link_for("security"), "https://imp.i384100.net/c/1/2/14726")
            got = courses.match_courses("python statistics")
            self.assertTrue(all(courses.allowed_course_url(c["url"], set(courses.DEFAULT_HOSTS)) for c in got))

    def test_plain_urls_match_the_allowlist(self):
        hosts = set(courses.DEFAULT_HOSTS)
        self.assertEqual(hosts, {"www.coursera.org", "coursera.pxf.io", "imp.i384100.net"})
        for _t, url, _rx in courses.COURSES.values():
            self.assertTrue(courses.allowed_course_url(url, hosts), url)

    def test_bad_json_falls_back(self):
        with mock.patch.dict(os.environ, {"COURSE_LINKS_JSON": "{not json"}):
            self.assertEqual(courses.link_for("python"), courses.COURSES["python"][1])

    def test_disclosure_wording(self):
        self.assertIn("not job promises", courses.DISCLOSURE)
        self.assertIn("commission", courses.DISCLOSURE)


class CourseApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_cards_carry_courses_and_stay_speculative(self):
        feed = self.client.get("/api/feed?section=careers&limit=60").json()
        cards = [p["career_card"] for p in feed if p.get("career_card")]
        self.assertTrue(cards)
        any_courses = False
        for cc in cards:
            self.assertTrue(cc["speculative"])
            self.assertIn("courses", cc)
            if cc["courses"]:
                any_courses = True
                self.assertEqual(cc["courses_disclosure"], courses.DISCLOSURE)
                for c in cc["courses"]:
                    self.assertTrue(c["url"].startswith("https://"))
            else:
                self.assertIsNone(cc["courses_disclosure"])
        self.assertTrue(any_courses)

    def test_index_renders_disclosure_hook(self):
        page = self.client.get("/").text
        self.assertIn("careerCourses", page)
        self.assertIn("careerCourseUrl", page)
        self.assertIn("sponsored", page)
        for host in ("www.coursera.org", "coursera.pxf.io", "imp.i384100.net"):
            self.assertIn(host, page)
        self.assertNotIn("/^https:\\/\\//", page)


if __name__ == "__main__":
    unittest.main()
