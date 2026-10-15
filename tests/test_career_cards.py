"""Career Cards posted by the inventor personas are sourced and still templatable.

app.gen() calls str.format on persona posts and replies. CAREER_POSTS stay
dicts so the card fields can be checked; _mk renders them to strings.
"""
import unittest

from personas_extra import (
    CAREER_LABELS,
    CAREER_POSTS,
    CAREER_REPLIES,
    CAREER_TO,
    NEW,
)

REQUIRED = (
    "title",
    "what_it_does",
    "who_hires",
    "skills_needed",
    "why_now",
    "timeline",
    "label",
    "citations",
)
# Matches create_career_card field limits. Extra fields are not sent to that API.
LIMITS = {
    "title": 120,
    "what_it_does": 500,
    "who_hires": 300,
    "skills_needed": 400,
    "why_now": 500,
}


class CareerCardTests(unittest.TestCase):
    def test_every_card_has_required_fields_label_and_citation(self):
        self.assertGreaterEqual(len(CAREER_POSTS), 1)
        labels = set()
        for card in CAREER_POSTS:
            self.assertIsInstance(card, dict)
            for key in REQUIRED:
                self.assertIn(key, card, card.get("title"))
            for key, limit in LIMITS.items():
                value = card[key]
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip(), card["title"])
                self.assertLessEqual(len(value), limit, card["title"] + " " + key)
            self.assertIn(card["label"], CAREER_LABELS)
            self.assertIsInstance(card["timeline"], str)
            self.assertTrue(card["timeline"].strip())
            self.assertIsInstance(card["citations"], list)
            self.assertGreaterEqual(len(card["citations"]), 1, card["title"])
            for url in card["citations"]:
                self.assertIsInstance(url, str)
                self.assertTrue(url.startswith("https://"), url)
                self.assertNotIn(" ", url)
            labels.add(card["label"])
            if card["label"] == "Established":
                self.assertTrue(
                    any(u.startswith("https://www.bls.gov/ooh/") for u in card["citations"]),
                    card["title"],
                )
            else:
                self.assertTrue(
                    any("arxiv.org/abs/" in u or "doi.org/" in u for u in card["citations"]),
                    card["title"],
                )
        self.assertEqual(labels, set(CAREER_LABELS))

    def test_replies_stay_on_career_cards_and_format(self):
        self.assertGreaterEqual(len(CAREER_REPLIES), 1)
        for reply in CAREER_REPLIES:
            self.assertIsInstance(reply, str)
            self.assertIn("{op}", reply)
            rendered = reply.format(op="@jobforge")
            self.assertNotIn("{", rendered)
            self.assertTrue(rendered.strip())
        # gen() looks up the parent bot's category, so every prefer-list category needs a pairing.
        for key in ("inventor1", "inventor2"):
            for cat in NEW[key]["prefer"]:
                self.assertIn(cat, CAREER_TO, cat)
        for cat, replies in CAREER_TO.items():
            self.assertIsInstance(replies, list)
            self.assertGreaterEqual(len(replies), 1)
            for reply in replies:
                rendered = reply.format(op="@novacareers")
                self.assertNotIn("{", rendered)
                self.assertIn("@novacareers", rendered)

    def test_inventor_templates_render_for_gen(self):
        for key in ("inventor1", "inventor2"):
            persona = NEW[key]
            self.assertGreaterEqual(len(persona["posts"]), len(CAREER_POSTS))
            for template, card in zip(persona["posts"], CAREER_POSTS):
                self.assertIsInstance(template, str)
                text = template.format(op="@friend", label="unused")
                self.assertIn("Career Card", text)
                self.assertIn(card["title"], text)
                self.assertIn(card["what_it_does"], text)
                self.assertIn(card["who_hires"], text)
                self.assertIn(card["skills_needed"], text)
                self.assertIn(card["why_now"], text)
                self.assertIn(card["timeline"], text)
                self.assertIn(card["label"], text)
                for url in card["citations"]:
                    self.assertIn(url, text)
                self.assertNotIn("{", text)


if __name__ == "__main__":
    unittest.main()
