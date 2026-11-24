"""Five-platform parity: every bundle must expose the same servers, tools, skills and agents.

    python3 -m unittest discover -s plugins/tests -v
"""

from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import platforms as P  # noqa: E402
import sync_platforms  # noqa: E402
import verify_platforms  # noqa: E402


class PlatformParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = verify_platforms.run(static=False)

    def test_five_platforms_registered(self):
        # The original five, plus Muse (added later; nothing was removed).
        self.assertEqual(sorted(P.PLATFORMS), ["antigravity", "claude-code", "codex", "muse", "qoder", "workbuddy"])

    def test_bundles_match_shared_source(self):
        self.assertEqual(sync_platforms.drift(), [])

    def test_every_check_passes_on_every_platform(self):
        failures = [
            f"{key}: {c['name']} - {c['detail']}"
            for key, plat in self.report["platforms"].items()
            for c in plat["checks"]
            if c["status"] == "fail"
        ]
        self.assertEqual(failures, [])

    def test_python_server_identical_everywhere(self):
        base = self.report["baseline"]["python"]
        self.assertGreaterEqual(len(base["tools"]), 23)
        self.assertTrue(base["instructions"], "initialize must carry the shared guardrail instructions")
        for key, plat in self.report["platforms"].items():
            if key == "claude-code-pane":  # a mod that calls the server; it launches none itself
                continue
            launched = [c for c in plat["checks"] if "(python)" in c["name"]]
            self.assertEqual([c["status"] for c in launched], ["pass"], key)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_bridge_server_identical_everywhere(self):
        self.assertGreater(len(self.report["baseline"]["bridge"]["tools"]), 0)
        for key, plat in self.report["platforms"].items():
            if key == "claude-code-pane":
                continue
            launched = [c for c in plat["checks"] if "(bridge)" in c["name"]]
            self.assertEqual([c["status"] for c in launched], ["pass"], key)

    def test_skill_sets_equal(self):
        sets = {
            key: sorted(p.name for p in (plat.root / "skills").iterdir() if (p / "SKILL.md").is_file())
            for key, plat in P.PLATFORMS.items()
        }
        shared = list(P.shared_skill_names())
        for key, names in sets.items():
            # A platform may add its own skills (Muse: po-muse-host) but never drop a shared one.
            self.assertEqual(names, sorted(shared + list(P.PLATFORMS[key].extra_skills)), key)

    def test_minimal_agent_keeps_body_and_drops_extra_keys(self):
        src = "---\nname: a\ndescription: >-\n  two\n  lines\nmodel: inherit\ncolor: blue\n---\n\nBody\n"
        self.assertEqual(sync_platforms.minimal_agent(src), "---\nname: a\ndescription: >-\n  two\n  lines\n---\n\nBody\n")


    def test_only_muse_turns_host_agent_mode_on(self):
        for key, plat in self.report["platforms"].items():
            names = [c["name"] for c in plat["checks"]]
            if key == "muse":
                self.assertTrue(any("`muse`-only mode" in n for n in names), names)
            elif key in P.PLATFORMS:
                self.assertIn("host-agent mode is off (providers come from .env)", names, key)

    def test_muse_launcher_env_file(self):
        import importlib.util
        import os
        import tempfile

        spec = importlib.util.spec_from_file_location("muse_launcher", P.PLATFORMS["muse"].root / "server" / "muse_launcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper-orchestra.env"
            path.write_text("# comment\nPO_TEST_MUSE_A=one\nPO_TEST_MUSE_B='two words'\nbad line\n1BAD=x\n", encoding="utf-8")
            os.environ["PO_TEST_MUSE_A"] = "kept"
            try:
                mod.load_env_file(path)
                self.assertEqual(os.environ["PO_TEST_MUSE_A"], "kept")  # the environment wins
                self.assertEqual(os.environ["PO_TEST_MUSE_B"], "two words")
                self.assertNotIn("1BAD", os.environ)
            finally:
                os.environ.pop("PO_TEST_MUSE_A", None)
                os.environ.pop("PO_TEST_MUSE_B", None)
            mod.load_env_file(Path(tmp) / "missing.env")  # no file: no error

    def test_pane_mod_is_checked(self):
        pane = self.report["platforms"]["claude-code-pane"]
        self.assertEqual([c["status"] for c in pane["checks"]], ["pass", "pass", "pass"])


if __name__ == "__main__":
    unittest.main()
