"""Offline tests for po_reference_sync (Zotero mocked, Overleaf against a local bare git repo).

    python3 -m unittest discover -s plugins/tests -v
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SERVER = Path(__file__).resolve().parents[1] / "shared" / "mcp_server" / "paper_orchestra_mcp.py"

ZOTERO_JSON = json.dumps([
    {"key": "ABCD1234", "data": {"itemType": "journalArticle", "title": "Deep learning", "date": "2015-05-28",
                                 "creators": [{"creatorType": "author", "firstName": "Yann", "lastName": "LeCun"},
                                              {"creatorType": "author", "firstName": "Yoshua", "lastName": "Bengio"}],
                                 "publicationTitle": "Nature", "DOI": "10.1038/nature14539", "abstractNote": "Review.",
                                 "tags": [{"tag": "dl"}], "extra": "arXiv: 1234.5678"}},
    {"key": "NOTE0001", "data": {"itemType": "note", "note": "skip me"}},
])
ZOTERO_BIB = "@article{lecun2015,\n title={Deep learning}\n}\n\n@article{old1,\n title={Already there}\n}\n"


def load_server(data_dir: str):
    with mock.patch.dict(os.environ, {"PAPER_ORCHESTRA_PLUGIN_DATA": data_dir, "PAPER_ORCHESTRA_DB": os.path.join(data_dir, "t.db")}):
        spec = importlib.util.spec_from_file_location("po_mcp_sync_under_test", SERVER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, mod.RuntimeContext()


def git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd, check=True, capture_output=True)


class ReferenceSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.mod, self.ctx = load_server(str(self.root / "data"))
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "main.tex").write_text("\\documentclass{article}\\begin{document}Hi\\end{document}\n", encoding="utf-8")
        (self.run_dir / "refs.bib").write_text("@article{old1,\n title={Already there}\n}\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, **args):
        return self.mod.tool_reference_sync(self.ctx, args)

    def zotero(self):
        def fake(url, headers, timeout):
            self.assertTrue(url.startswith("http://127.0.0.1:23119/api/users/0/"), url)
            self.assertNotIn("Zotero-API-Key", headers)
            return ZOTERO_BIB if "format=bibtex" in url else ZOTERO_JSON
        return mock.patch.multiple(self.mod, _http_get_text=mock.Mock(side_effect=fake), _zotero_local_alive=mock.Mock(return_value=True))

    def test_status_never_returns_secret_values(self):
        with mock.patch.dict(os.environ, {"ZOTERO_API_KEY": "zk-SECRETVALUE", "ZOTERO_USER_ID": "42", "OVERLEAF_GIT_TOKEN": "olp_SECRETVALUE"}), \
                mock.patch.object(self.mod, "_zotero_local_alive", return_value=False):
            out = self.call(action="status")
        self.assertTrue(out["zotero"]["webApiConfigured"])
        self.assertTrue(out["overleaf"]["tokenConfigured"])
        self.assertNotIn("SECRETVALUE", json.dumps(out))

    def test_zotero_search_and_import_keep_user_notes(self):
        with self.zotero():
            found = self.call(action="zotero_search", query="deep")
            self.assertEqual(found["count"], 1)
            self.assertEqual(found["items"][0]["authors"], "Yann LeCun; Yoshua Bengio")
            self.assertEqual(found["items"][0]["year"], "2015")
            first = self.call(action="zotero_import", query="deep")
            self.assertEqual([(i["id"], i["updated"]) for i in first["items"]], [("zotero-ABCD1234", False)])
            with self.ctx.connect() as conn:
                conn.execute("UPDATE library_items SET ai_notes = 'my note', read_status = 'read' WHERE id = 'zotero-ABCD1234'")
                conn.commit()
            second = self.call(action="zotero_import", query="deep")
            self.assertTrue(second["items"][0]["updated"])
            with self.ctx.connect() as conn:
                row = conn.execute("SELECT * FROM library_items WHERE id = 'zotero-ABCD1234'").fetchone()
        self.assertEqual((row["ai_notes"], row["read_status"], row["doi"], row["arxiv_id"], row["folder"]),
                         ("my note", "read", "10.1038/nature14539", "1234.5678", "Zotero"))

    def test_zotero_bibtex_merges_only_new_keys_with_backup(self):
        with self.zotero():
            out = self.call(action="zotero_bibtex", run_dir=str(self.run_dir))
        self.assertEqual(out["added"], ["lecun2015"])
        self.assertEqual(out["alreadyPresent"], ["old1"])
        text = (self.run_dir / "refs.bib").read_text(encoding="utf-8")
        self.assertEqual(self.mod._bib_keys(text), ["old1", "lecun2015"])
        self.assertTrue(Path(out["backup"]).is_file())

    def test_zotero_rejects_bad_keys_and_web_without_credentials(self):
        with self.zotero(), self.assertRaises(RuntimeError):
            self.call(action="zotero_search", item_keys=["../../etc"])
        with mock.patch.dict(os.environ, {"ZOTERO_API_KEY": "", "ZOTERO_USER_ID": ""}), self.assertRaises(RuntimeError):
            self.call(action="zotero_search", source="web")

    def test_library_to_ris(self):
        with self.zotero():
            self.call(action="zotero_import")
        out = self.call(action="library_to_ris", out_path=str(self.root / "lib.ris"))
        ris = Path(out["outPath"]).read_text(encoding="utf-8")
        self.assertIn("TY  - JOUR\nTI  - Deep learning\nAU  - Yann LeCun\nAU  - Yoshua Bengio\nPY  - 2015", ris)
        self.assertTrue(ris.rstrip().endswith("ER  -"))

    def test_overleaf_requires_token_and_valid_project_id(self):
        with mock.patch.dict(os.environ, {"OVERLEAF_GIT_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                self.call(action="overleaf_link", project_id="0123456789abcdef01234567")
            with self.assertRaises(RuntimeError):
                self.call(action="overleaf_link", project_id="not-an-id")
            with self.assertRaises(RuntimeError):
                self.call(action="overleaf_link", remote_url="https://user:pw@example.com/x.git")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_overleaf_round_trip_against_local_remote(self):
        bare = self.root / "remote.git"
        git("init", "--bare", "-b", "master", str(bare))
        seed = self.root / "seed"
        git("clone", str(bare), str(seed))
        (seed / "main.tex").write_text("remote version\n", encoding="utf-8")
        (seed / "only_remote.tex").write_text("keep me\n", encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("commit", "-m", "seed", cwd=seed)
        git("push", "origin", "HEAD", cwd=seed)

        remote = str(bare)
        linked = self.call(action="overleaf_link", remote_url=remote)
        self.assertEqual(linked["files"], ["main.tex", "only_remote.tex"])

        # Pull into the run: main.tex differs, so nothing is copied without allow_overwrite.
        held = self.call(action="overleaf_pull", remote_url=remote, run_dir=str(self.run_dir))
        self.assertFalse(held["applied"])
        self.assertEqual(held["wouldModify"], ["main.tex"])
        self.assertFalse((self.run_dir / "only_remote.tex").exists())
        done = self.call(action="overleaf_pull", remote_url=remote, run_dir=str(self.run_dir), allow_overwrite=True)
        self.assertTrue(done["applied"])
        self.assertEqual((self.run_dir / "main.tex").read_text(encoding="utf-8"), "remote version\n")
        self.assertIn("\\documentclass", (Path(done["backupDir"]) / "main.tex").read_text(encoding="utf-8"))

        # Push: dry run first, then confirmed. Remote-only files survive.
        (self.run_dir / "main.tex").write_text("local edit\n", encoding="utf-8")
        (self.run_dir / "only_remote.tex").unlink()
        (self.run_dir / "main.pdf").write_bytes(b"%PDF compiled output must not be pushed")
        dry = self.call(action="overleaf_push", remote_url=remote, run_dir=str(self.run_dir))
        self.assertFalse(dry["pushed"])
        self.assertEqual((dry["add"], dry["modify"]), (["refs.bib"], ["main.tex"]))
        self.assertEqual(subprocess.run(["git", "log", "--oneline"], cwd=bare, capture_output=True, text=True).stdout.count("\n"), 1)
        sent = self.call(action="overleaf_push", remote_url=remote, run_dir=str(self.run_dir), confirm_push=True)
        self.assertTrue(sent["pushed"])
        check = self.root / "check"
        git("clone", str(bare), str(check))
        self.assertEqual(sorted(p.name for p in check.iterdir() if p.name != ".git"), ["main.tex", "only_remote.tex", "refs.bib"])
        self.assertEqual((check / "main.tex").read_text(encoding="utf-8"), "local edit\n")
        again = self.call(action="overleaf_push", remote_url=remote, run_dir=str(self.run_dir), confirm_push=True)
        self.assertFalse(again["pushed"])


if __name__ == "__main__":
    unittest.main()
