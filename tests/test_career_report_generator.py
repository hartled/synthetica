"""Career-report dataset, generator, escaping, and PDF. No network and no Stripe."""

import json
import re
import unittest

import career_data
import reports

BANNED = re.compile(
    r"\$|\bpercent\b|\bmedian\b|\baverage (wage|salary|pay)\b|\bper year\b|\bgrowth rate\b",
    re.I,
)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _strings(value)


def _intake(**overrides):
    data = {
        "current_job": "Junior data analyst",
        "skills": "Excel, SQL, basic Python",
        "education": "B.A. in Sociology",
        "interests": "machine learning, healthcare, teaching",
        "country": "United States",
        "region": "Texas",
    }
    data.update(overrides)
    cleaned, errors = reports.validate_intake(data)
    assert not errors, errors
    return cleaned


class DatasetTests(unittest.TestCase):
    def test_every_entry_has_source_link_and_label(self):
        self.assertTrue(career_data.check_dataset())
        entries = career_data.REAL_CAREERS + career_data.SPECULATIVE_CAREERS
        self.assertGreaterEqual(len(career_data.REAL_CAREERS), 8)
        self.assertGreaterEqual(len(career_data.SPECULATIVE_CAREERS), 3)
        for entry in entries:
            self.assertIn(entry["label"], career_data.LABELS)
            self.assertTrue(entry["sources"])
            for source in entry["sources"]:
                self.assertTrue(source["name"])
                self.assertTrue(source["url"].startswith("https://"))
            for cid in entry["course_ids"]:
                course = career_data.COURSES[cid]
                self.assertTrue(course["url"].startswith("https://"))
                self.assertTrue(course["title"] and course["provider"])
        for entry in career_data.SPECULATIVE_CAREERS:
            self.assertEqual(entry["label"], "Speculative")
            self.assertTrue(any("arxiv.org/" in s["url"] or "doi.org/" in s["url"] for s in entry["sources"]))
        for entry in career_data.REAL_CAREERS:
            self.assertIn(entry["label"], ("Established", "Emerging research"))
            self.assertTrue(any("bls.gov/" in s["url"] or "onetonline.org/" in s["url"] for s in entry["sources"]))
        for blob in _strings({"courses": career_data.COURSES, "real": career_data.REAL_CAREERS, "spec": career_data.SPECULATIVE_CAREERS}):
            self.assertIsNone(BANNED.search(blob), blob)


class GeneratorTests(unittest.TestCase):
    def test_sample_shape_is_deterministic_and_has_no_pay_figures(self):
        first = reports.sample_report()
        second = reports.sample_report()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertTrue(first["sample"])
        self.assertEqual(first["persona_name"], "Alex Rivera")
        self.assertGreaterEqual(len(first["careers"]), 3)
        self.assertLessEqual(len(first["careers"]), 5)
        self.assertGreaterEqual(len(first["speculative"]), 2)
        self.assertLessEqual(len(first["speculative"]), 3)
        titles = [item["title"] for item in first["careers"]]
        self.assertIn("Data scientist", titles)
        data = next(item for item in first["careers"] if item["title"] == "Data scientist")
        self.assertIn("Statistics", data["missing"])
        self.assertIn("Python", data["have"])
        self.assertTrue(data["sources"] and data["courses"] and data["steps"])
        for item in first["speculative"]:
            self.assertEqual(item["label"], "Speculative")
            self.assertEqual(item["disclaimer"], "Speculative — not a job promise")
            self.assertTrue(any("arxiv.org/" in s["url"] for s in item["sources"]))
        joined = "\n".join(_strings(first))
        self.assertIsNone(BANNED.search(joined), joined)
        self.assertIn("Texas", first["location_note"])
        self.assertIn("does not copy pay", first["location_note"])

    def test_security_intake_ranks_the_security_occupation_first(self):
        report = reports.generate(_intake(
            current_job="Help desk",
            skills="Networking, security, risk assessment, Python, writing",
            education="Certificate",
            interests="security, privacy, safety",
            country="United States",
            region="Ohio",
        ))
        self.assertEqual(report["careers"][0]["id"], "security-analyst")
        self.assertTrue(report["united_states"])
        self.assertIn("Ohio", report["location_note"])
        self.assertNotIn("wage", report["location_note"].lower())

    def test_non_us_note_does_not_invent_local_stats(self):
        report = reports.generate(_intake(
            current_job="Teacher",
            skills="Writing, public speaking",
            education="B.Ed.",
            interests="teaching, writing",
            country="Canada",
            region="Ontario",
        ))
        self.assertFalse(report["united_states"])
        note = report["location_note"]
        self.assertIn("Canada", note)
        self.assertIn("Ontario", note)
        self.assertIn("does not estimate wages", note)
        self.assertNotIn("Ontario has", note)
        for item in report["careers"] + report["speculative"]:
            self.assertTrue(any("Canada" in step or "United States" in step for step in item["steps"]))

    def test_html_escapes_intake(self):
        report = reports.generate(_intake(
            current_job="<script>alert(1)</script>",
            skills='"><img src=x onerror=alert(1)>',
            education="B.A.",
            interests="teaching",
            country="Canada",
            region="Ontario",
        ))
        page = reports.render_report_html(report, kind="paid")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn('"><img', page)
        self.assertIn("&lt;img", page)
        self.assertIn('type="search"', page)
        self.assertIn('href="#main"', page)
        self.assertIn("max-width:768px", page)
        self.assertIn('aria-label="Open search"', page)

    def test_validation_rejects_empty_and_huge_fields(self):
        _, errors = reports.validate_intake({
            "current_job": " ",
            "skills": "",
            "education": "x",
            "interests": "ai",
            "country": "US",
            "region": "TX",
        })
        self.assertIn("current_job", errors)
        self.assertIn("skills", errors)
        _, bad_email = reports.validate_email("<script>@example.com")
        self.assertTrue(bad_email)
        email, err = reports.validate_email("  Person@Example.com ")
        self.assertIsNone(err)
        self.assertEqual(email, "person@example.com")

    def test_pdf_is_a_pdf_and_contains_the_title(self):
        report = reports.sample_report()
        blob = reports.render_pdf(report, kind="sample")
        self.assertTrue(blob.startswith(b"%PDF"))
        self.assertIn(b"%%EOF", blob)
        self.assertGreater(len(blob), 2000)
        title = report["careers"][0]["title"]
        self.assertTrue(
            title.encode("utf-8") in blob or title.encode("utf-16-be") in blob,
            "career title was not embedded in the PDF",
        )
        hostile = reports.generate(_intake(
            current_job="<script>alert(1)</script>",
            skills="Python, SQL",
            education="B.A.",
            interests="machine learning",
            country="United States",
            region="Texas",
        ))
        hostile_pdf = reports.render_pdf(hostile, kind="paid")
        self.assertTrue(hostile_pdf.startswith(b"%PDF"))
        self.assertNotIn(b"<script>alert(1)</script>", hostile_pdf)


if __name__ == "__main__":
    unittest.main()
