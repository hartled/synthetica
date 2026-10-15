"""Mentor runner: card selection, dedupe caps, and cited on-topic replies.

The HTTP API is mocked. No test posts to the live site or prints a raw key.
"""

import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from bots.mentor_runner import (
    CARD_POSTS,
    FEW_NEW_CARDS,
    MENTORS,
    MODES,
    POST_CAP,
    REPLY_CAP,
    VOICE,
    Api,
    already_replied,
    fit,
    is_career_card,
    jittered_interval,
    mask_key,
    parse_args,
    pick_card_post,
    plan_mentor,
    render_reply,
    resolve_keys,
    retry_after_seconds,
    run_once,
    select_cards,
    topic_for,
)

import re

CITATION_RE = re.compile(
    r"https://(?:www\.bls\.gov/ooh/[a-z0-9./-]+\.htm|arxiv\.org/abs/\d{4}\.\d{4,5}|doi\.org/10\.\S+)"
)
LABEL_RE = re.compile(r"\[(?:Established|Emerging research|Speculative)\]")


def card(post_id, title, *, what="guides the work", who="city desks", skills="plain writing",
         why="because the work is changing", handle="novacareers", replies=None, **extra):
    post = {
        "id": post_id,
        "parent_id": None,
        "ad_id": None,
        "post_type": "career_card",
        "content": title,
        "bot": {"id": 1, "handle": handle, "name": handle},
        "replies": list(replies or []),
        "career_card": {
            "title": title,
            "what_it_does": what,
            "who_hires": who,
            "skills_needed": skills,
            "why_now": why,
            "speculative": True,
            "label": "Speculative — not a job promise",
        },
    }
    post.update(extra)
    return post


def reply_by(handle, reply_id=900):
    return {"id": reply_id, "post_type": "post", "content": "noted", "bot": {"handle": handle}}


class SelectionTests(unittest.TestCase):
    def test_keeps_career_cards_and_skips_ads_own_cards_and_non_cards(self):
        cards = [
            card(5, "Drone Shepherd"),
            card(4, "Memory Archivist", handle="forge_mentor"),
            {"id": 17, "ad_id": 2, "post_type": "post", "parent_id": None,
             "bot": {"handle": "sponsored"}, "career_card": None, "replies": []},
            card(3, "Model Whisperer", post_type="post"),
            card(2, "Climate Loss Explainer", parent_id=1),
            {"id": 1, "post_type": "career_card", "ad_id": None, "parent_id": None,
             "bot": {"handle": "jobforge"}, "career_card": {"title": ""}, "replies": []},
        ]
        self.assertTrue(is_career_card(cards[0]))
        self.assertFalse(is_career_card(cards[2]))
        self.assertFalse(is_career_card(cards[3]))
        picked = select_cards(cards, "forge_mentor", {"replied": {}, "posted_titles": {}}, limit=10)
        self.assertEqual([p["id"] for p in picked], [5])

    def test_preserves_feed_order(self):
        cards = [card(30, "Newer"), card(10, "Older")]
        picked = select_cards(cards, "ledger_lin", {"replied": {}, "posted_titles": {}}, limit=5)
        self.assertEqual([p["id"] for p in picked], [30, 10])

    def test_topic_follows_the_card(self):
        self.assertEqual(topic_for(card(1, "Drone Shepherd", what="stray drones")).name, "drone")
        self.assertEqual(topic_for(card(2, "Qubit Reliability Steward")).name, "quantum-hardware")
        self.assertEqual(topic_for(card(3, "Quantum Algorithm Translator")).name, "quantum")
        self.assertEqual(topic_for(card(4, "Memory Archivist")).name, "archive")
        self.assertEqual(topic_for(card(5, "Model Whisperer")).name, "model")
        self.assertEqual(topic_for(card(6, "Climate Loss Explainer")).name, "climate")
        self.assertEqual(topic_for(card(7, "Robot Etiquette Tutor")).name, "robot")
        self.assertEqual(topic_for(card(8, "Something Else")).name, "general")


class DedupeCapTests(unittest.TestCase):
    def setUp(self):
        self.cards = [card(n, f"Card {n}") for n in (9, 8, 7, 6, 5)]

    def test_reply_cap_is_two(self):
        actions = plan_mentor("forge_mentor", self.cards, {"replied": {}, "posted_titles": {}})
        replies = [a for a in actions if a.kind == "reply"]
        self.assertEqual(len(replies), REPLY_CAP)
        self.assertEqual([a.post_id for a in replies], [9, 8])
        self.assertFalse(any(a.kind == "card" for a in actions))

    def test_api_reply_skips_even_when_state_is_empty(self):
        cards = [
            card(9, "Answered", replies=[reply_by("mentor_mae")]),
            card(8, "Open A"),
            card(7, "Open B"),
            card(6, "Open C"),
        ]
        self.assertTrue(already_replied(cards[0], "mentor_mae", set()))
        picked = select_cards(cards, "mentor_mae", {"replied": {}, "posted_titles": {}}, limit=5)
        self.assertEqual([p["id"] for p in picked], [8, 7, 6])

    def test_state_file_skips_when_the_api_has_no_reply(self):
        state = {"replied": {"blueprint_bea": [9, 8]}, "posted_titles": {}}
        picked = select_cards(self.cards, "blueprint_bea", state, limit=5)
        self.assertEqual([p["id"] for p in picked], [7, 6, 5])

    def test_duplicate_ids_count_once(self):
        cards = [card(4, "Once"), card(4, "Once again"), card(3, "Next")]
        picked = select_cards(cards, "ledger_lin", {"replied": {}, "posted_titles": {}}, limit=5)
        self.assertEqual([p["id"] for p in picked], [4, 3])

    def test_few_unanswered_cards_allow_one_post(self):
        one = [card(3, "Only")]
        actions = plan_mentor("mentor_mae", one, {"replied": {}, "posted_titles": {}})
        self.assertEqual(len([a for a in actions if a.kind == "reply"]), 1)
        posts = [a for a in actions if a.kind == "card"]
        self.assertEqual(len(posts), POST_CAP)
        self.assertLess(len(one), FEW_NEW_CARDS)

    def test_zero_cards_still_allows_one_post(self):
        actions = plan_mentor("forge_mentor", [], {"replied": {}, "posted_titles": {}})
        self.assertEqual([a.kind for a in actions], ["card"])

    def test_posted_title_and_feed_title_are_not_repeated(self):
        state = {"replied": {}, "posted_titles": {"ledger_lin": [CARD_POSTS["ledger_lin"][0]["title"]]}}
        first = pick_card_post("ledger_lin", [], state)
        self.assertEqual(first.title, CARD_POSTS["ledger_lin"][1]["title"])
        existing = [card(1, CARD_POSTS["ledger_lin"][1]["title"], handle="someone")]
        self.assertIsNone(pick_card_post("ledger_lin", existing, state))

    def test_many_cards_do_not_add_a_post(self):
        actions = plan_mentor("blueprint_bea", self.cards, {"replied": {}, "posted_titles": {}})
        self.assertEqual([a.kind for a in actions], ["reply", "reply"])


class ReplyTemplateTests(unittest.TestCase):
    def _sample(self, title="Drone Shepherd"):
        return card(
            11,
            title,
            what="NEARMISS guides stray drones back to a lane",
            who="PAYERSECTOR municipal desks",
            skills="SKILLTOKEN radio manner",
            why="sidewalks are filling up",
        )

    def test_every_voice_and_mode_cites_and_stays_on_topic(self):
        post = self._sample()
        seen = {}
        for handle in MENTORS:
            seen[handle] = []
            for mode in MODES[handle]:
                text = render_reply(handle, mode, post)
                seen[handle].append(text)
                self.assertLessEqual(len(text), 500, text)
                self.assertRegex(text, LABEL_RE)
                urls = CITATION_RE.findall(text)
                self.assertEqual(urls, [text.split()[-1]], text)
                self.assertTrue(text.split()[-1].startswith("http"), text)
                self.assertIn("Drone Shepherd", text)
                self.assertIn(VOICE[handle], text.casefold())
                if mode in ("steelman", "rival"):
                    self.assertIn("NEARMISS", text)
                if mode == "critique":
                    self.assertIn("PAYERSECTOR", text)
                if mode == "skills":
                    self.assertIn("SKILLTOKEN", text)
                if mode == "rival":
                    self.assertIn(topic_for(post).rival, text)
        # Distinct voices: no two mentors share a reply for the same mode index.
        for left in range(len(MENTORS)):
            for right in range(left + 1, len(MENTORS)):
                self.assertNotEqual(seen[MENTORS[left]][0], seen[MENTORS[right]][0])

    def test_long_fields_keep_the_citation_and_the_cap(self):
        post = card(
            2,
            "T" * 120,
            what="W" * 400,
            who="H" * 200,
            skills="S" * 200,
        )
        for handle in MENTORS:
            for mode in MODES[handle]:
                text = render_reply(handle, mode, post)
                self.assertLessEqual(len(text), 500)
                self.assertRegex(text, CITATION_RE)
                self.assertTrue(text.endswith(CITATION_RE.search(text).group(0)) or "…" in text)

    def test_topic_specific_sources(self):
        drone = self._sample()
        text = render_reply("forge_mentor", "critique", drone)
        self.assertIn("logisticians", text)
        self.assertIn("https://www.bls.gov/ooh/business-and-financial/logisticians.htm", text)

        quantum = card(4, "Qubit Reliability Steward", what="turns calibration logs into a decision",
                       who="hardware labs", skills="statistics")
        text = render_reply("ledger_lin", "critique", quantum)
        self.assertIn("https://arxiv.org/abs/1801.00862", text)
        self.assertIn("Qubit Reliability Steward", text)
        self.assertIn("100-qubit", text)

        model = card(5, "Model Whisperer", what="explains odd model output", who="product teams",
                     skills="evaluation design")
        text = render_reply("ledger_lin", "steelman", model)
        self.assertIn("https://arxiv.org/abs/2303.10130", text)
        self.assertIn("not a hire", text)
        self.assertIn("Model Whisperer", text)

    def test_optional_cards_are_speculative_and_cited(self):
        titles = []
        for handle, cards in CARD_POSTS.items():
            self.assertGreaterEqual(len(cards), 1)
            for item in cards:
                titles.append(item["title"])
                for key, limit in (
                    ("title", 120),
                    ("what_it_does", 500),
                    ("who_hires", 300),
                    ("skills_needed", 400),
                    ("why_now", 500),
                ):
                    self.assertLessEqual(len(item[key]), limit)
                self.assertRegex(item["why_now"], LABEL_RE)
                self.assertRegex(item["why_now"], CITATION_RE)
                blob = " ".join(item.values()).casefold()
                self.assertTrue(
                    "speculative" in blob or "not a " in blob or "not an " in blob,
                    item["title"],
                )
                self.assertIn(VOICE[handle], blob)
        self.assertEqual(len(titles), len(set(titles)))

    def test_fit_preserves_a_trailing_url(self):
        url = "https://doi.org/10.1257/jep.33.2.3"
        text = fit(("word " * 200) + url)
        self.assertLessEqual(len(text), 500)
        self.assertTrue(text.endswith(url))


class ApiBehaviorTests(unittest.TestCase):
    def test_retry_after_is_honored_and_capped(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "7"}), 7)
        self.assertEqual(retry_after_seconds({"retry-after": "9999"}), 120)
        self.assertEqual(retry_after_seconds({}), 5)

        calls = {"n": 0}

        def transport(method, url, headers, body, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return 429, {"Retry-After": "4"}, {"detail": "rate limit"}
            return 200, {}, []

        slept = []
        api = Api("https://example.test", transport=transport, sleep=slept.append, pause=0)
        status, payload = api.request("GET", "/api/career-cards", params={"limit": 5})
        self.assertEqual(status, 200)
        self.assertEqual(payload, [])
        self.assertEqual(slept, [4])
        self.assertEqual(calls["n"], 2)

    def test_invalid_key_skips_mentor_without_leaking_the_key(self):
        secret = "syn_supersecretvalue99"
        seen = {"posts": 0}

        def transport(method, url, headers, body, timeout):
            if method == "POST":
                seen["posts"] += 1
                self.assertNotIn(secret, url)
                return 401, {}, {"detail": "invalid API key"}
            return 200, {}, []

        err = io.StringIO()
        api = Api("https://example.test", transport=transport, sleep=lambda _n: None, pause=0)
        cards = [card(3, "Only Card")]
        run_once(
            api,
            {"mentor_mae": secret},
            {"replied": {}, "posted_titles": {}},
            dry_run=False,
            cards=cards,
            out=lambda _text: None,
            err=err,
        )
        warning = err.getvalue()
        self.assertIn("skipping mentor_mae", warning)
        self.assertIn("401", warning)
        self.assertIn(mask_key(secret), warning)
        self.assertNotIn(secret, warning)
        self.assertNotIn("supersecretvalue", warning)
        # Reply plus optional card would be two writes; 401 stops the rest.
        self.assertEqual(seen["posts"], 1)

    def test_dry_run_does_not_post_or_write_state(self):
        def transport(method, url, headers, body, timeout):
            if method == "POST":
                raise AssertionError("dry-run posted")
            return 200, {}, [
                card(8, "Drone Shepherd"),
                card(7, "Memory Archivist"),
                card(6, "Model Whisperer"),
            ]

        err = io.StringIO()
        lines = []
        api = Api("https://example.test", transport=transport, sleep=lambda _n: None, pause=0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            run_once(
                api,
                None,
                {"replied": {}, "posted_titles": {}},
                dry_run=True,
                state_path=path,
                out=lines.append,
                err=err,
            )
            self.assertFalse(path.exists())
        text = "\n".join(lines)
        self.assertIn("forge_mentor", text)
        self.assertIn("reply post_id=8", text)
        self.assertNotIn("syn_", text)
        self.assertNotIn("post career_card", text)

    def test_bad_key_json_does_not_echo_the_secret(self):
        secret = "syn_thisisasecretkey99"
        with self.assertRaises(SystemExit) as caught:
            resolve_keys("{not-json " + secret, Path("/no/such/keys.json"), dry_run=False)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("thisisasecret", str(caught.exception))

    def test_missing_keys_dry_run_plans_all_mentors(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "keys.json"
            self.assertIsNone(resolve_keys(None, missing, dry_run=True))
            with self.assertRaises(SystemExit):
                resolve_keys(None, missing, dry_run=False)

    def test_keys_file_round_trip_masks_in_any_warning_path(self):
        secret = "syn_filekeyvalue42"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            path.write_text(json.dumps({"forge_mentor": secret, "stranger": "syn_othersecret00"}), encoding="utf-8")
            loaded = resolve_keys(None, path, dry_run=False)
        self.assertEqual(set(loaded), {"forge_mentor"})
        self.assertEqual(loaded["forge_mentor"], secret)
        self.assertNotIn(secret, mask_key(secret))
        self.assertTrue(mask_key(secret).startswith("syn_"))
        self.assertTrue(mask_key(secret).endswith("42"))

    def test_jitter_stays_within_ten_percent(self):
        rng = random.Random(0)
        vals = [jittered_interval(3600, rng) for _ in range(40)]
        self.assertTrue(all(3240 <= value <= 3960 for value in vals))
        self.assertGreater(max(vals) - min(vals), 1)
        with self.assertRaises(ValueError):
            jittered_interval(0, rng)

    def test_cli_requires_once_or_loop(self):
        args = parse_args(["--once", "--dry-run"])
        self.assertTrue(args.once)
        self.assertTrue(args.dry_run)
        with self.assertRaises(SystemExit):
            parse_args(["--once", "--loop"])


class GitignoreTests(unittest.TestCase):
    def test_keys_and_state_are_ignored(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("bots/supportive_peers/keys.json", text)
        self.assertIn("bots/supportive_peers/state.json", text)


if __name__ == "__main__":
    unittest.main()
