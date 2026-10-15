"""SECRET_KEY and ADMIN_KEY fail closed only in a deployed environment."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _boot(extra):
    env = os.environ.copy()
    for key in ("SECRET_KEY", "ADMIN_KEY", "FORCE_HTTPS", "RENDER", "SYNTHETICA_ENV", "COOKIE_SECURE"):
        env.pop(key, None)
    env.update(extra)
    env["TICK_SECONDS"] = "100000"
    env["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="synth-secret-"), "t.db")
    return subprocess.run(
        [sys.executable, "-c", "import app"],
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

    def test_dev_mode_allows_force_https_without_secrets(self):
        res = _boot({"SYNTHETICA_ENV": "dev", "FORCE_HTTPS": "true"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WARNING: SECRET_KEY is unset", res.stdout)

    def test_force_https_without_secret_key_refuses(self):
        res = _boot({"FORCE_HTTPS": "true", "ADMIN_KEY": "admin-present"})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("SECRET_KEY", res.stderr)

    def test_render_without_admin_key_refuses(self):
        res = _boot({"RENDER": "true", "SECRET_KEY": "secret-present"})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ADMIN_KEY", res.stderr)

    def test_production_with_both_keys_starts(self):
        res = _boot({"FORCE_HTTPS": "true", "RENDER": "true", "SECRET_KEY": "s", "ADMIN_KEY": "a"})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("dev-only default", res.stdout)

    def test_render_yaml_keeps_generate_value(self):
        text = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
        self.assertEqual(text.count("generateValue: true"), 2)
        self.assertIn("SECRET_KEY", text)
        self.assertIn("ADMIN_KEY", text)
