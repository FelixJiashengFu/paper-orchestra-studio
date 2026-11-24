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
        self.assertEqual(sorted(P.PLATFORMS), ["antigravity", "claude-code", "codex", "qoder", "workbuddy"])

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
        first = next(iter(sets.values()))
        for key, names in sets.items():
            self.assertEqual(names, first, key)
        self.assertEqual(first, list(P.shared_skill_names()))

    def test_minimal_agent_keeps_body_and_drops_extra_keys(self):
        src = "---\nname: a\ndescription: >-\n  two\n  lines\nmodel: inherit\ncolor: blue\n---\n\nBody\n"
        self.assertEqual(sync_platforms.minimal_agent(src), "---\nname: a\ndescription: >-\n  two\n  lines\n---\n\nBody\n")


    def test_pane_mod_is_checked(self):
        pane = self.report["platforms"]["claude-code-pane"]
        self.assertEqual([c["status"] for c in pane["checks"]], ["pass", "pass", "pass"])


if __name__ == "__main__":
    unittest.main()
