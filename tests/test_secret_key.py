"""SECRET_KEY and named admin actors fail closed only in a deployed environment."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _boot(extra, code="import app"):
    env = os.environ.copy()
    for key in (
        "SECRET_KEY", "ADMIN_KEY", "ADMIN_KEY_D", "ADMIN_KEY_CEO",
        "FORCE_HTTPS", "RENDER", "SYNTHETICA_ENV", "COOKIE_SECURE",
    ):
        env.pop(key, None)
    env.update(extra)
    env["TICK_SECONDS"] = "100000"
    env["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="synth-secret-"), "t.db")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


class SecretKeyTests(unittest.TestCase):
    def test_local_without_flags_warns_and_starts(self):
        res = _boot({})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WARNING: SECRET_KEY is unset", res.stdout)
        self.assertIn("WARNING: admin_actors is empty", res.stdout)

    def test_dev_mode_allows_force_https_without_secrets(self):
        res = _boot({"SYNTHETICA_ENV": "dev", "FORCE_HTTPS": "true"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WARNING: SECRET_KEY is unset", res.stdout)
        self.assertIn("WARNING: admin_actors is empty", res.stdout)

    def test_force_https_without_secret_key_refuses(self):
        res = _boot({"FORCE_HTTPS": "true", "ADMIN_KEY_D": "d", "ADMIN_KEY_CEO": "c"})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("SECRET_KEY", res.stderr)

    def test_render_without_named_admin_keys_refuses(self):
        res = _boot({"RENDER": "true", "SECRET_KEY": "secret-present"})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ADMIN_KEY_D", res.stderr)
        self.assertIn("ADMIN_KEY_CEO", res.stderr)

    def test_render_with_only_legacy_admin_key_refuses(self):
        res = _boot({"RENDER": "true", "SECRET_KEY": "secret-present", "ADMIN_KEY": "legacy-singleton"})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ADMIN_KEY_D", res.stderr)
        self.assertNotIn("legacy-singleton", res.stderr)
        self.assertNotIn("legacy-singleton", res.stdout)

    def test_render_with_identical_admin_keys_refuses(self):
        res = _boot({
            "RENDER": "true", "SECRET_KEY": "secret-present",
            "ADMIN_KEY_D": "same-key", "ADMIN_KEY_CEO": "same-key",
        })
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("two different keys", res.stderr)
        self.assertNotIn("same-key", res.stderr)

    def test_production_with_named_keys_stores_hashes_only(self):
        code = textwrap.dedent("""
            import os, sqlite3, hashlib
            import app
            c = sqlite3.connect(os.environ["DATABASE_PATH"])
            rows = list(c.execute("SELECT slug, key_hash, display_name FROM admin_actors ORDER BY slug"))
            assert [r[0] for r in rows] == ["ceo", "d"], rows
            by = {r[0]: r for r in rows}
            assert by["d"][1] == hashlib.sha256(os.environ["ADMIN_KEY_D"].encode()).hexdigest()
            assert by["ceo"][1] == hashlib.sha256(os.environ["ADMIN_KEY_CEO"].encode()).hexdigest()
            assert by["d"][2] == "D" and by["ceo"][2] == "Synthetica CEO"
            blob = " ".join(str(x) for r in rows for x in r)
            assert os.environ["ADMIN_KEY_D"] not in blob
            assert os.environ["ADMIN_KEY_CEO"] not in blob
            print("hashed")
        """)
        res = _boot({
            "FORCE_HTTPS": "true", "RENDER": "true", "SECRET_KEY": "s",
            "ADMIN_KEY_D": "d-key-value", "ADMIN_KEY_CEO": "ceo-key-value",
        }, code)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("hashed", res.stdout)
        self.assertNotIn("dev-only default", res.stdout)
        self.assertNotIn("d-key-value", res.stdout)
        self.assertNotIn("ceo-key-value", res.stdout)

    def test_second_boot_does_not_require_env_keys(self):
        dbpath = os.path.join(tempfile.mkdtemp(prefix="synth-admin-reboot-"), "t.db")

        def run(extra, program="import app"):
            env = os.environ.copy()
            for key in (
                "SECRET_KEY", "ADMIN_KEY", "ADMIN_KEY_D", "ADMIN_KEY_CEO",
                "FORCE_HTTPS", "RENDER", "SYNTHETICA_ENV", "COOKIE_SECURE",
            ):
                env.pop(key, None)
            env.update(extra)
            env["TICK_SECONDS"] = "100000"
            env["DATABASE_PATH"] = dbpath
            return subprocess.run(
                [sys.executable, "-c", program], cwd=ROOT, env=env,
                capture_output=True, text=True,
            )

        seeded = run({
            "RENDER": "true", "SECRET_KEY": "secret-present",
            "ADMIN_KEY_D": "d-key-value", "ADMIN_KEY_CEO": "ceo-key-value",
        })
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        code = textwrap.dedent("""
            import os, sqlite3
            import app
            c = sqlite3.connect(os.environ["DATABASE_PATH"])
            n = c.execute("SELECT COUNT(*) FROM admin_actors WHERE active=1").fetchone()[0]
            assert n == 2, n
            print("still-active")
        """)
        again = run({"RENDER": "true", "SECRET_KEY": "secret-present"}, code)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("still-active", again.stdout)
        self.assertNotIn("d-key-value", again.stdout)

    def test_dev_does_not_bind_legacy_admin_key(self):
        code = textwrap.dedent("""
            import os, sqlite3
            import app
            c = sqlite3.connect(os.environ["DATABASE_PATH"])
            n = c.execute("SELECT COUNT(*) FROM admin_actors").fetchone()[0]
            assert n == 0, n
            print("empty")
        """)
        res = _boot({"ADMIN_KEY": "legacy-singleton"}, code)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("empty", res.stdout)
        self.assertIn("Ignoring ADMIN_KEY", res.stdout)
        self.assertNotIn("legacy-singleton", res.stdout)

    def test_production_does_not_read_admin_key_file(self):
        path = os.path.join(ROOT, ".admin_key")
        secret = "file-admin-key-not-for-prod"
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(secret)
            res = _boot({"RENDER": "true", "SECRET_KEY": "secret-present"})
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("ADMIN_KEY_D", res.stderr)
            self.assertNotIn(secret, res.stdout)
            self.assertNotIn(secret, res.stderr)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_dev_ignores_admin_key_file(self):
        path = os.path.join(ROOT, ".admin_key")
        secret = "file-admin-key-dev-ignored"
        code = textwrap.dedent("""
            import os, sqlite3
            import app
            c = sqlite3.connect(os.environ["DATABASE_PATH"])
            n = c.execute("SELECT COUNT(*) FROM admin_actors").fetchone()[0]
            assert n == 0, n
            print("empty")
        """)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(secret)
            res = _boot({}, code)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("empty", res.stdout)
            self.assertIn("Ignoring .admin_key", res.stdout)
            self.assertNotIn(secret, res.stdout)
            self.assertNotIn(secret, res.stderr)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_render_yaml_names_both_actors(self):
        text = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
        self.assertEqual(text.count("generateValue: true"), 1)
        self.assertIn("SECRET_KEY", text)
        self.assertIn("ADMIN_KEY_D", text)
        self.assertIn("ADMIN_KEY_CEO", text)
        self.assertNotIn("- key: ADMIN_KEY\n", text)
