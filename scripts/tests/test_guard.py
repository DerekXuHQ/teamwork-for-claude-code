try:
    from ._base import *
except ImportError:
    from _base import *


class Guard(unittest.TestCase):
    """T16: private words/paths never leave the machine in commits."""

    SECRET = "Zorblax"  # stands in for a private product name; deliberately made up

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.proj = root / "proj"
        self.proj.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.proj / ".gitignore").write_text(".teamwork/\n")
        (self.proj / "a.txt").write_text("hello\n")
        self.commit("init")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.tw("init", "proj")
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"# private\n- {self.SECRET.lower()}\npath: secrets/*\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *x):
        return subprocess.run(["git", "-C", str(self.proj), *x], check=True, capture_output=True, text=True)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)

    def tw(self, *args):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), *args], cwd=self.proj,
                              capture_output=True, text=True)

    def test_init_ignores_private_files(self):
        ig = (self.proj / ".teamwork" / ".gitignore").read_text()
        self.assertIn("PRIVATE.md", ig)
        self.assertIn("private/", ig)
        self.assertTrue((self.proj / ".teamwork" / "private").is_dir())

    def test_clean_passes(self):
        (self.proj / "b.txt").write_text("nothing to see\n")
        self.commit("b")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("guard: ok", r.stdout)

    def test_hit_in_worktree_and_history_masked(self):
        (self.proj / "b.txt").write_text(f"look at {self.SECRET}\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("worktree b.txt", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())  # the word itself is never printed
        self.commit("add b")
        self.assertIn("history", self.tw("guard").stdout)

    def test_hit_only_in_history(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET} notes\n")
        self.commit("oops")
        (self.proj / "b.txt").write_text("cleaned\n")
        self.commit("clean up")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("history", r.stdout)
        self.assertNotIn("worktree b.txt", r.stdout)

    def test_private_path(self):
        (self.proj / "secrets").mkdir()
        (self.proj / "secrets" / "k.txt").write_text("x\n")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1)
        self.assertIn("(name hidden)", r.stdout)  # the whole matching path stays private
        self.assertNotIn("secrets", r.stdout)

    def guard(self, *a):
        return self.tw("guard", *a)

    def tw_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        return tw

    def test_t25_escaped_and_packed_words(self):
        import bz2, io, lzma, zipfile, zlib
        w = "蓝鲸星"
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", "<w:r><w:t>Zor</w:t></w:r><w:r><w:t>blax</w:t></w:r>")
        stream = zlib.compress(b"BT /F1 12 Tf [(Zor) -20 (blax)] TJ ET")
        files = {
            "fixture.json": json.dumps({"name": w}).encode(),  # ensure_ascii: \u84dd...
            "notes.rtf": b"{\\rtf1 \\uc1\\u34013 ?\\u40120 ?\\u26143 ?}",
            "page.html": "&#34013;&#x9cb8;&#26143;".encode(),
            "link.txt": b"https://x.invalid/?q=%5A%6F%72%62%6C%61%78",
            "report.docx": docx.getvalue(),
            "report.pdf": b"%PDF-1.4\n1 0 obj<</Filter/FlateDecode>>stream\n" + stream + b"\nendstream endobj",
            "dump.bz2": bz2.compress(b"abc" * 50 + b" Zorblax" * 20),
            "dump.xz": lzma.compress(b"abc" * 50 + b" Zorblax" * 20),
        }
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(f"- Zorblax\n- {w}\n")
        for name, data in files.items():
            (self.proj / name).write_bytes(data)
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        (self.proj / "plain.json").write_text(json.dumps({"a": "caf\u00e9 \u00e5"}) + " &amp; %41 <b>ok</b>\n")
        self.commit("clean escapes")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_t25_empty_pre_push_input_fails_closed(self):
        r = self.pre_push("")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("input=$(cat)", r.stdout + r.stderr)
        # the hook install.py writes owns stdin: empty means git had nothing to push ("Everything up-to-date")
        for stdin in ("", "\n"):
            r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push", "--remote",
                                "origin", "--stdin-owned"], cwd=self.proj, input=stdin, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to push", r.stdout)

    def test_t25_paths_and_quoted_entries_stay_whole(self):
        tw = self.tw_mod()
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text('- /home/zorbuser\n- /Users/zorbuser\n- ~/quixbar-notes\n- C:\\Users\\zorb\n- "Quixbar, Ltd"\n- `a/b`\n- Zorblax/Quixbar\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for t in ("/home/zorbuser", "/Users/zorbuser", "~/quixbar-notes", "C:\\Users\\zorb", "Quixbar, Ltd", "a/b"):
            self.assertIn(t, terms)
        for t in ("Users", "zorbuser", "quixbar-notes", "Ltd", "a", "b"):
            self.assertNotIn(t, terms)
        self.assertIn("Zorblax", terms)  # a plain 'X/Y' line still adds both
        pm.write_text("1. /srv/zorblax, /opt/quixbar\n- /\n- ~\n- /a/zorb,/b/quix\n")  # a list of paths: each one whole
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/srv/zorblax", "/opt/quixbar", "/a/zorb", "/b/quix"])  # a bare / or ~ never counts
        # a note after a path is dropped, as for any entry; a quoted entry keeps its own '#'; one character never counts
        pm.write_text('- /home/zorbuser - my home\n- /srv/quixbar (old box)\n- ~/lanjing: notes\n- "Quixbar #1"\n'
                      '- "x"\n')
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(terms, ["/home/zorbuser", "/srv/quixbar", "~/lanjing", "Quixbar #1"])

    def test_t25_home_path_is_built_in(self):
        home = Path(self._tmp.name) / "home" / "quixuser"
        home.mkdir(parents=True)
        env = {**os.environ, "HOME": str(home)}
        (self.proj / "cfg.json").write_text(json.dumps({"hook": f"python3 {home}/x/tw.py context"}))
        self.commit("absolute path")
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--base", "origin/main"],
                           cwd=self.proj, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("quixuser", r.stdout)

    def test_t25_hashed_list_and_cloud_kit(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- Zorblax\n- 蓝鲸星\n")
        j1 = json.loads(self.guard("--hash-list").stdout)
        j2 = json.loads(self.guard("--hash-list").stdout)
        self.assertNotEqual(j1["salt"], j2["salt"])  # a new salt every time
        blob = json.dumps(j1)
        for w in ("zorblax", "蓝鲸星", "\\u84dd"):
            self.assertNotIn(w, blob.lower())
        self.assertGreater(len(j1["hashes"]), 2)  # decoys pad the list
        self.assertNotIn("len", "".join(j1))  # no lengths stored
        kit = self.guard("--hash-list", "--kit").stdout
        self.assertNotIn("Zorblax", kit)
        root = Path(self._tmp.name)
        (root / "tw-guard.py").write_text(kit)
        (root / "h.json").write_text(json.dumps(j1))
        cloud = root / "cloud"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(cloud)], check=True)
        g = lambda *x: subprocess.run(["git", "-C", str(cloud), "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                                       *x], capture_output=True, text=True)
        g("checkout", "-qb", "work")
        (cloud / "n.md").write_text("the 蓝鲸星 plan\n")
        g("add", "-A")
        g("commit", "-qm", "n")
        kitrun = lambda: subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        hashed = lambda: subprocess.run([sys.executable, str(TW), "guard", "--hashed", str(root / "h.json"),
                                         "--repo", str(cloud), "--base", "origin/main"], cwd=cloud,
                                        capture_output=True, text=True)
        self.assertEqual(kitrun().returncode, 1)
        r = hashed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # works without any .teamwork folder
        self.assertNotIn("蓝鲸星", r.stdout)
        g("reset", "-q", "--hard", "origin/main")
        (cloud / "ok.md").write_text("Quixbar is fine\n")
        g("add", "-A")
        g("commit", "-qm", "ok")
        self.assertEqual(kitrun().returncode, 0, kitrun().stdout)
        self.assertEqual(hashed().returncode, 0)
        # run from a subfolder: an untracked file at the top is still read
        (cloud / "sub").mkdir()
        (cloud / "top.md").write_text("蓝鲸星\n")
        r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), "origin/main"], cwd=cloud / "sub",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        (cloud / "top.md").unlink()
        # any git error fails closed: a base that does not exist, or no repo at all
        for args, cwd in ((["origin/nope"], cloud), (["origin/main"], root)):
            r = subprocess.run([sys.executable, "-I", str(root / "tw-guard.py"), *args], cwd=cwd,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, (args, r.stdout, r.stderr))
            self.assertNotIn("guard: ok", r.stdout)
        # decoys are random values, never hashes of a string: hex-heavy files (lock files, SHAs) do not trip them
        tw = self.tw_mod()
        import secrets
        noise = "\n".join(secrets.token_hex(20) for _ in range(3000)).encode()
        for _ in range(10):
            j = json.loads(self.guard("--hash-list").stdout)
            self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(noise.lower()))
            self.assertTrue(all(len(p) == 3 for p in j["pre"]))  # 4096 buckets: the prefix is not named
        # no home path in the list: it would give the cloud thread a guessable user name
        home = root / "home" / "quixuser"
        home.mkdir(parents=True)
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--hash-list"], cwd=self.proj,
                           env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
        j = json.loads(r.stdout)
        self.assertFalse(tw.HashedList(j["salt"], j["hashes"], j["pre"])(str(home).lower().encode()))

    def test_t25_install_keeps_absolute_paths_out_of_commits(self):
        inst = SCRIPTS / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        run = lambda: subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir",
                                      str(cfg), "--project", str(self.proj), "--pre-push"],
                                     capture_output=True, text=True)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.proj / ".claude" / "settings.json").exists())
        self.assertTrue((self.proj / ".claude" / "settings.local.json").exists())
        self.assertIn(".claude/settings.local.json", (self.proj / ".git" / "info" / "exclude").read_text())
        self.assertNotIn("settings", self.git("status", "--porcelain").stdout)
        # a tracked hooks folder would carry the hook (absolute paths) into a commit: refused
        (self.proj / ".git" / "hooks" / "pre-push").unlink()
        self.git("config", "core.hooksPath", ".githooks")
        r = run()
        self.assertIn("refused", r.stdout)
        self.assertFalse((self.proj / ".githooks" / "pre-push").exists())
        # another tool's hook: the printed lines save stdin once
        self.git("config", "--unset", "core.hooksPath")
        (self.proj / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\ngit lfs pre-push \"$@\"\n")
        out = run().stdout
        self.assertIn("input=$(cat)", out)
        self.assertIn("--stdin-owned || exit 1", out)  # the saved input is the guard's alone: up-to-date pushes pass

    def test_round5_star_globs_keep_their_stars(self):
        pm = self.proj / ".teamwork" / "PRIVATE.md"
        pm.write_text("- quixbar\npath: *.pem\n- path: notes_*\n- path: `*.key`\n**path: dump_*.md**\n")
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        _, paths, _ = tw.load_private(self.proj / ".teamwork")
        self.assertEqual(paths, ["*.pem", "notes_*", "*.key", "dump_*.md"])
        for name in ["server.pem", "notes_client.md", "deep/id.key", "dump_x.md"]:
            f = self.proj / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n")
            self.commit("add " + name)
            r = self.guard("--base", "origin/main")
            self.assertEqual(r.returncode, 1, name + ": " + r.stdout + r.stderr)
            self.git("reset", "-q", "--hard", "origin/main")
            self.git("clean", "-qfd")
        self.assertEqual(self.guard("--base", "origin/main").returncode, 0)

    def test_review_binary_and_encodings(self):
        (self.proj / "l1.txt").write_bytes(("caf\xe9 " + self.SECRET).encode("latin-1"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "l1.txt").unlink()
        (self.proj / "u16.txt").write_bytes(("x " + self.SECRET).encode("utf-16"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "u16.txt").unlink()
        (self.proj / "img.png").write_bytes(b"\x89PNG\x00\x01" + self.SECRET.encode() + b"\x00")
        self.commit("bin")
        (self.proj / "img.png").unlink()
        self.commit("rm bin")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("history", r.stdout)

    def test_review_gitattributes_nodiff(self):
        (self.proj / ".gitattributes").write_text("*.dat -diff\n")
        (self.proj / "x.dat").write_text(self.SECRET + "\n")
        self.commit("dat")
        (self.proj / "x.dat").unlink()
        self.commit("rm dat")
        self.assertEqual(self.guard().returncode, 1)

    def test_review_names_and_chinese(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("- 蓝鲸星\n1. Quixbar\n**Snorf**  # product\n")
        for name, body in (("文件.txt", "Quixbar inside"), ("蓝鲸星.txt", "ok"), ("notes-snorf.md", "ok")):
            (self.proj / name).write_text(body)
            r = self.guard()
            self.assertEqual(r.returncode, 1, name)
            for w in ("蓝鲸星", "quixbar", "snorf"):
                self.assertNotIn(w, r.stdout.lower())  # nothing printed in clear, file names included
            (self.proj / name).unlink()
        self.assertEqual(self.guard().returncode, 0)

    def test_review_ref_not_checked_out(self):
        self.git("checkout", "-q", "-b", "feat")
        (self.proj / "f.txt").write_text(self.SECRET)
        self.commit("feat")
        self.git("checkout", "-q", "main")
        r = self.guard("--base", "main", "--ref", "feat")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tree feat", r.stdout)

    def test_review_missing_list_fails_closed(self):
        (self.proj / ".teamwork" / "PRIVATE.md").unlink()
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no private list", r.stdout + r.stderr)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("# nothing private\n")
        self.assertEqual(self.guard().returncode, 0)

    def test_review_branch_name(self):
        r = subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push"], cwd=self.proj,
                           input=f"refs/heads/x {'a' * 40} refs/heads/{self.SECRET.lower()}-wip {'0' * 40}\n",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ref name", r.stdout)

    def test_pre_push_hook_blocks(self):
        inst = SCRIPTS / "install.py"
        cfg = Path(self._tmp.name) / "cfg"
        r = subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                            "--project", str(self.proj), "--pre-push"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        hook = self.proj / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.exists())
        self.assertIn('--remote "$1"', hook.read_text())
        lines = hook.read_text().splitlines()  # an older teamwork hook (no --remote) is upgraded in place
        hook.write_text("\n".join(lines[:2] + [lines[2].replace(' --remote "$1"', "")]) + "\n")
        subprocess.run([sys.executable, str(inst), "--apply", "--link", "--no-allow", "--config-dir", str(cfg),
                        "--project", str(self.proj), "--pre-push"], capture_output=True, text=True, check=True)
        self.assertIn('--remote "$1"', hook.read_text())
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        self.commit("c")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("guard: FAILED", push.stdout + push.stderr)
        self.git("reset", "-q", "--hard", "HEAD~1")
        (self.proj / "d.txt").write_text("fine\n")
        self.commit("d")
        push = subprocess.run(["git", "-C", str(self.proj), "push", "origin", "main"], capture_output=True, text=True)
        self.assertEqual(push.returncode, 0, push.stdout + push.stderr)


    def pre_push(self, stdin, remote="origin"):
        return subprocess.run([sys.executable, str(TW), "-C", str(self.proj), "guard", "--pre-push",
                               *(["--remote", remote] if remote else [])],
                              cwd=self.proj, input=stdin, capture_output=True, text=True)

    def test_round2_first_push_to_another_remote(self):
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("old leak, already on origin")
        self.git("push", "-q", "origin", "main")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        other = Path(self._tmp.name) / "mirror.git"
        subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
        self.git("remote", "add", "mirror", str(other))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        line = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        self.assertEqual(self.pre_push(line, "mirror").returncode, 1)  # mirror never had the old commit
        self.assertEqual(self.pre_push(line, None).returncode, 1)  # remote unknown: whole history
        self.assertEqual(self.pre_push(line, "origin").returncode, 0)  # origin already has all of it

    def test_round2_encodings_everywhere(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text("蓝鲸星\nQuixbär\n")
        for name, data in (("gb.txt", "注意 蓝鲸星".encode("gb18030")), ("l1.txt", "x Quixbär".encode("latin-1")),
                           ("u16.txt", "x 蓝鲸星".encode("utf-16-le")), ("up.txt", "QUIXBÄR".encode("utf-8"))):
            (self.proj / name).write_bytes(data)
            self.commit(name)
            self.assertEqual(self.guard().returncode, 1, f"worktree/tree {name}")
            (self.proj / name).unlink()
            self.commit("rm " + name)
            r = self.guard()
            self.assertEqual(r.returncode, 1, f"history {name}")
            self.assertIn("history", r.stdout)
            self.git("reset", "-q", "--hard", "origin/main")

    def test_round2_list_formats(self):
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "| word | why |\n|---|---|\n| Zeta9 | client |\n"
            "- [ ] Snorf\n- [x] Blorpo\nQuixbar, Flimwax\nFlurbo - our client\nGronky: internal\n")
        terms = None
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zeta9", "Snorf", "Blorpo", "Quixbar", "Flimwax", "Flurbo", "Gronky"):
            self.assertIn(w, terms)
        self.assertNotIn("word", terms)  # table header skipped
        self.assertTrue(tw._path_rule_hit("secrets/k.txt", "/Secrets/"))
        self.assertTrue(tw._path_rule_hit("docs/Notes.md", "notes.md"))
        self.assertTrue(tw._path_rule_hit("a/b/key.pem", "**/*.pem"))
        self.assertFalse(tw._path_rule_hit("docs/secrets/x", "/secrets/"))

    def test_round2_author_tag_and_branch_names(self):
        (self.proj / "e.txt").write_text("e\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", f"user.email={self.SECRET.lower()}@example.invalid",
                 "commit", "-qm", "clean message")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.git("reset", "-q", "--hard", "origin/main")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "v1", "-m",
                 f"release for {self.SECRET}")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        tag = self.git("rev-parse", "v1").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {'0' * 40}\n").returncode, 1)
        self.git("checkout", "-q", "-b", f"{self.SECRET.lower()}-wip")
        r = self.guard()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ref name", r.stdout)
        self.assertNotIn(self.SECRET.lower(), r.stdout.lower())

    def test_round2_private_folder_name_masked(self):
        (self.proj / ".teamwork" / "private" / "Quixbar-notes.txt").write_text("x")
        self.git("add", "-f", ".teamwork/private/Quixbar-notes.txt")  # someone force-added it
        r = self.guard()
        self.assertEqual(r.returncode, 1)
        self.assertIn("private/*****", r.stdout)
        self.assertNotIn("quixbar", r.stdout.lower())


    def test_round3_stale_remote_url(self):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", f"fix for {self.SECRET} customer")
        self.git("push", "-q", "origin", "main")  # on the old (private) remote
        public = Path(self._tmp.name) / "public.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("remote", "set-url", "origin", str(public))  # refs/remotes/origin/* are now stale
        head = self.git("rev-parse", "HEAD").stdout.strip()
        r = self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n", "origin")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_round4_history_zip_pushurl_list_encoding(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", f"<w>hello {self.SECRET} quixbar</w>" * 50)
        (self.proj / "d.docx").write_bytes(buf.getvalue())
        self.commit("add doc")
        self.git("rm", "-q", "d.docx")
        self.commit("drop doc")
        r = self.tw("guard")
        self.assertEqual(r.returncode, 1, r.stdout)  # only in history, compressed
        self.git("reset", "-q", "--hard", "origin/main")
        # pushurl differs from the fetch URL: compare against what the push server has
        (self.proj / "b.txt").write_text(f"{self.SECRET}\n")
        self.commit("leak")
        (self.proj / "b.txt").write_text("clean\n")
        self.commit("clean")
        self.git("push", "-q", "origin", "main")
        public = Path(self._tmp.name) / "public2.git"
        subprocess.run(["git", "init", "-q", "--bare", str(public)], check=True)
        self.git("config", "remote.origin.pushurl", str(public))
        head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(self.pre_push(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n").returncode, 1)
        self.git("config", "--unset", "remote.origin.pushurl")
        # a list saved with a BOM or as UTF-16 still works; undecodable fails closed
        self.git("reset", "-q", "--hard", "HEAD~2")
        (self.proj / "c.txt").write_text(f"{self.SECRET}\n")
        priv = self.proj / ".teamwork" / "PRIVATE.md"
        for data in (b"\xef\xbb\xbf" + self.SECRET.encode(), self.SECRET.encode("utf-16"), b"\xff\xfa\x80"):
            priv.write_bytes(data)
            r = self.tw("guard")
            self.assertEqual(r.returncode, 1, data[:4])
            self.assertIn("UTF-8" if data[:2] == b"\xff\xfa" else "FAILED", r.stdout + r.stderr)

    def test_round3_tags_on_blobs_and_nested(self):
        blob = subprocess.run(["git", "-C", str(self.proj), "hash-object", "-w", "--stdin"],
                              input=f"{self.SECRET} plan", capture_output=True, text=True).stdout.strip()
        self.git("tag", "lw", blob)
        r = self.pre_push(f"refs/tags/lw {blob} refs/tags/lw {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        g = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        self.git(*g, "tag", "-a", "inner", "-m", f"{self.SECRET} inner")
        self.git(*g, "tag", "-a", "outer", "inner", "-m", "clean")
        outer = self.git("rev-parse", "outer").stdout.strip()
        self.git("tag", "-d", "inner")
        self.assertEqual(self.pre_push(f"refs/tags/outer {outer} refs/tags/outer {'0' * 40}\n").returncode, 1)

    def test_round3_parser_nfd_replace_zip_symlink(self):
        import importlib.util, unicodedata, zipfile, io, os as _os
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        (self.proj / ".teamwork" / "PRIVATE.md").write_text(
            "Name: Zorblax\n> Quixbar\n* [x] ~~Snorfle~~\n<!-- Blorpo -->\nFlimwax/Gronky\nCrème\n")
        terms, _, _ = tw.load_private(self.proj / ".teamwork")
        for w in ("Zorblax", "Quixbar", "Snorfle", "Blorpo", "Flimwax", "Gronky"):
            self.assertIn(w, terms)
        (self.proj / "n.txt").write_text(unicodedata.normalize("NFD", "la Crème"))
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "n.txt").unlink()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w>Zorblax roadmap</w>" * 50)
        (self.proj / "r.docx").write_bytes(buf.getvalue())
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "r.docx").unlink()
        _os.symlink("/nonexistent/Quixbar-dir", self.proj / "ln")
        self.assertEqual(self.guard().returncode, 1)
        (self.proj / "ln").unlink()
        self.assertEqual(self.guard().returncode, 0)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
                 "-m", "Zorblax secret msg")
        bad = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--amend",
                 "--allow-empty", "-m", "clean copy")
        good = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("reset", "-q", "--hard", bad)
        self.git("replace", bad, good)  # local view says clean; the server would get the original
        self.assertEqual(self.guard().returncode, 1)

class GuardSafeShapes(unittest.TestCase):
    """T36: nothing the skill tells a session to run, and nothing its scripts run, trips other plugins' guards."""

    # The shapes common safety hooks ask about (recursive force delete, hard reset, forced clean / branch delete /
    # push, checkout over the work tree). Written as regexes, so this file does not match itself.
    G = r"git\s+(?:-C\s+\S+\s+)?"  # `git -C <path> ...` is the protocol's own spelling
    SHAPES = [r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f", r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]", r"\brm\s+.*-[rR]\b.*\s-f\b",
              r"\brm\s+.*-f\b.*\s-[rR]\b", r"\brm\s+.*--recursive\b.*--force\b", r"\brm\s+.*--force\b.*--recursive\b",
              G + r"reset\s+(.*\s)?--hard", G + r"clean\s+(.*\s)?-[a-zA-Z]*f", G + r"push\s+.*--force([^-]|$)",
              G + r"push(.*\s)?-f[a-zA-Z]*\b", G + r"push\s+(.*\s)?\+\S", G + r"checkout\s+(.*\s)?--\s+\.",
              G + r"branch\s+(.*\s)?-D"]
    SKIP = (".git", ".teamwork", ".claude", "docs/field-tests")  # local settings are rules, not commands; field tests quote what went wrong, word for word

    def test_no_guard_tripping_shapes(self):
        root = REPO
        hits = []
        paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".txt", ".sh", ".json",
                                                                                  ".yml", ".yaml")
                       and not any(str(p.relative_to(root)).startswith(x) for x in self.SKIP))
        self.assertIn(root / "SKILL.md", paths)
        for path in paths:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for rx in self.SHAPES:
                    if re.search(rx, line, 0 if "branch" in rx else re.I):
                        hits.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_shapes_still_match_what_they_should(self):
        bad = ["rm " + "-rf /tmp/x", "rm -r " + "-f x", "rm " + "-Rf x", "rm " + "-vrf x", "git reset " + "--hard HEAD",
               "git clean " + "-fd", "git clean -xd" + "f", "git branch " + "-D x", "git push origin " + "--for" + "ce",
               "git push " + "-" + "f origin x", "git push -" + "fu origin x", "git push origin " + "+main",
               "git -C /tmp/q push " + "-" + "f origin x", "git -C /tmp/q reset " + "--hard", "git reset HEAD~1 " + "--hard"]
        ok = ["rm -r /tmp/x", "git push --force-with-lease origin x", "git branch -d x", "git switch -C x main",
              "git worktree remove --force /tmp/wt"]
        hit = lambda c: any(re.search(rx, c, 0 if "branch" in rx else re.I) for rx in self.SHAPES)
        self.assertTrue(all(hit(c) for c in bad))
        self.assertFalse([c for c in ok if hit(c)])

    def test_protocol_lists_the_shapes_and_alternatives(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        env = {k: v for k, v in os.environ.items() if k != "TEAMWORK_DIR"}
        subprocess.run([sys.executable, str(TW), "init", "demo", "--goal", "g"], cwd=d, env=env, check=True,
                       capture_output=True)
        subprocess.run([sys.executable, str(TW), "thread", "add", "ui"], cwd=d, env=env, check=True, capture_output=True)
        wp = subprocess.run([sys.executable, str(TW), "worker-prompt", "ui"], cwd=d, env=env, capture_output=True,
                            text=True).stdout
        for want in ("safety hooks", "`git push --force-with-lease`", "`git switch -C", "Never disguise",
                     "this list included"):
            self.assertIn(want, wp)
        skill = (REPO / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Command shapes that trip guards", skill)
        self.assertIn("never edit the user's plugins or hooks", skill)

class BashGuard(unittest.TestCase):
    """T36: the PreToolUse hook judges each command's argv: big decisions ask, wiping / or ~ is denied, everything
    else (words in messages, heredocs, grep arguments included) passes silently."""

    F, FORCE, HARD, RF = "-" + "f", "--for" + "ce", "--" + "hard", "-r" + "f"

    def judge(self, cmd):
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else None

    def test_ordinary_work_passes(self):
        F, FORCE, HARD = self.F, self.FORCE, self.HARD
        for cmd in ("git status", "git -C /tmp/q commit -m x", "git push -u origin tw/q/ui",
                    "git push --force-with-lease origin tw/q/ui", "git push --force-with-lease=tw/q/ui:abc origin x",
                    "git -C /tmp/q push origin x", "git branch -d tw/q/ui", "git switch -C tw/q/ui origin/main",
                    "git restore --staged quix.py", "git clean -n", "rm zorb.txt", f"rm {F} zorb.txt", "ls /tmp",
                    "python3 scripts/tw.py ready", "launchctl list", "brew install quixbar", "git checkout main",
                    "git stash list", "git worktree list", "mkdir -p /tmp/z && cd /tmp/z && git init -q"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_t42_no_heredoc_inside_arithmetic_or_expansions(self):
        """`<<` in (( )), $(( )), $[ ], ${ } is a shift or text: the next lines are commands; a quoted `)` does not
        end $( )."""
        HARD = self.HARD
        for cmd in (f"(( x = 1 << 2 ))\ngit reset {HARD}\n2", f"for (( i=1<<2; i<9; i++ )); do :; done\ngit reset {HARD}\n2",
                    f"echo ${{x:-<<EOF}}\ngit reset {HARD}\nEOF}}", f"echo $[ 1 <<2 ]\ngit reset {HARD}\n2",
                    f'echo "$((1<<2))"\ngit reset {HARD}\n2', f"echo $(echo ')' ; git reset {HARD})",
                    f'echo $(echo ")" ; git reset {HARD})', f"echo ${{x:-$(git reset {HARD})}}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertIsNone(self.judge(f"echo $(cat <<EOF\ngit reset {HARD}\nEOF\n)\nls"))  # a real heredoc in $( )

    def test_t42_heredoc_delimiters_read_as_the_shell_does(self):
        HARD = self.HARD
        for cmd in (f'cat <<E"O"F\nbody\nEOF\ngit reset {HARD}', f'cat <<"EO F"\nx\nEO F\ngit reset {HARD}',
                    f"cat << EOF\ngit reset {HARD}", f'cat <<A <<B\na\nA\nb\nB\ngit reset {HARD}',
                    f"cat <<$'EOF'\nbody\nEOF\ngit reset {HARD}\n$EOF", f'cat <<$"EOF"\nb\nEOF\ngit reset {HARD}',
                    f"cat <<$'E\\x4fF'\nhi\nEOF\ngit reset {HARD}\nE\\x4fF", f'cat <<"E\\$F"\nhi\nE$F\ngit reset {HARD}\nE\\$F'):
            self.assertEqual(self.judge(cmd), "ask", cmd)  # after the body, or an unclosed one: judged
        for cmd in (f"cat <<'EOF'\ngit reset {HARD}\nEOF\nls", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF\nls"):
            self.assertIsNone(self.judge(cmd), cmd)  # inside a closed body: data

    def test_words_in_messages_heredocs_and_greps_pass(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        words = (f"launchctl and DROP TABLE, sudo, shutdown, reboot, pkill, rm {RF} /, git push {FORCE}, push {F}u, "
                 f"+main, :old, reset {HARD}, clean {F}, worktree remove, stash drop, branch -" + "D, checkout -- ., "
                 "restore ., brew uninstall")
        for cmd in (f'git commit -m "T36: {words}"', f'git -C /tmp/zorblax commit -m "fix checkout flow -- handle cart"',
                    f'git -C /tmp/zorblax commit -m "docs: worktree remove step; stash drop; restore .env; push {F}ail"',
                    f'git -C /tmp/zorblax commit -m "retry push with + backoff; reset with {HARD}; clean {F}x"',
                    f'git -C /tmp/zorblax log --grep "use branch -" + "D sparingly"', f"grep -n '{words}' notes.md",
                    f"cat > /tmp/zorblax.md <<'EOF'\n{words}\nrm {RF} ~\nEOF\necho done",
                    f'python3 scripts/tw.py ask "{words}"', f"echo 'git push {FORCE}' | wc -c"):
            self.assertIsNone(self.judge(cmd), cmd)

    def test_big_decisions_ask_in_every_spelling(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"git push {FORCE} origin main", f"git push origin main {FORCE}", f"git push {F} origin x",
                    f"git push {F}u origin x", "git push origin " + "+main", "git push origin :old",
                    "git push origin --delete old", "git push --mirror", f"git -C /tmp/q push {F} origin x",
                    f"git -c core.x=y -C /tmp/q push origin x {FORCE}", f"git reset {HARD} HEAD~1",
                    f"git reset HEAD~1 {HARD}", f"git -c a=b reset {HARD}", "git clean -xd" + "f",
                    f"git clean -d {F}", "git branch -" + "D x", "git branch --delete " + FORCE + " x",
                    "git branch " + F + " main HEAD~3", "git checkout .", "git checkout -- quix.py",
                    f"git checkout {F} main", "git switch --discard-changes main", "git restore quix.py",
                    "git restore .", "git stash drop", "git -C /tmp/q stash clear", "git worktree remove /tmp/w",
                    "rm -r /tmp/z", f"rm {RF} /tmp/z", "rm -R /tmp/z", "rm -vr" + "f /tmp/z", "/bin/rm -r /tmp/z",
                    "rm --recursive " + FORCE + " /tmp/z", "find /tmp/z -delete", "sudo ls", "shutdown -h now",
                    "launchctl unload x.plist", "launchctl kickstart -k system/x", "killall quixbar", "pkill zorb",
                    "diskutil eraseDisk x", "brew uninstall quixbar", "brew services restart quixbar",
                    "npm uninstall -g quixbar", "psql -c 'DROP TABLE zorb'", "gh repo delete quixbar/zorblax",
                    "gh repo edit q/z --visibility public", "gh api -X DELETE repos/q/z",
                    "cd /tmp/q && git push origin x " + FORCE, "git status; git reset " + HARD,
                    "X=1 git push " + FORCE):
            self.assertEqual(self.judge(cmd), "ask", cmd)

    def test_wiping_root_or_home_is_denied(self):
        RF, F = self.RF, self.F
        for cmd in (f"rm {RF} /", f"rm {RF} /*", f"rm {RF} ~", f"rm {RF} ~/", f"rm {RF} ~/*", f'rm {RF} "$HOME"',
                    f"rm {RF} $HOME", "rm -R" + F[1] + " ${HOME}", f"rm -r {F} /", f"sudo rm {RF} /",
                    f"rm {RF} {Path.home()}"):
            self.assertEqual(self.judge(cmd), "deny", cmd)
        self.assertEqual(self.judge(f"rm {RF} ~/proj/tmp"), "ask")

    def test_r3_spellings(self):
        F, FORCE, HARD, RF = self.F, self.FORCE, self.HARD, self.RF
        for cmd in (f"if [ -d q ]; then rm {RF} q; fi", f"for f in a b; do rm {RF} $f; done", f'bash -c "rm {RF} q"',
                    "sh -lc 'git clean -xd" + "f'", f'eval "git reset {HARD}"', "x=`git clean -d" + F + "`",
                    f"echo $(git reset {HARD})", f"timeout 5 git reset {HARD}", f"nice -n 5 git reset {HARD}",
                    f"env -i FOO=1 git reset {HARD}", f"git push \\\n{FORCE} origin q", f"rm \\\n{RF} q",
                    "find . -execdir rm {} \\;", f"xargs -n 1 rm {RF}", f"sudo -u root git reset {HARD}",
                    f"{{ git reset {HARD}; }}", "git push origin " + "+HEAD:main", f"ls 2>&1 | head; git reset {HARD}"):
            self.assertEqual(self.judge(cmd), "ask", cmd)
        self.assertEqual(self.judge(f'bash -c "rm {RF} ~"'), "deny")
        for cmd in (f'git commit -m "T9: tidy guard\nrm {RF} build was slow"', "git commit -m 'subject\nsudo is not it'",
                    f'python3 tw.py ask "two lines\ngit clean -d{F} here?" --from ui',
                    'gh pr create --body "notes:\ngit branch -' + 'D old"', "find . -name '*.py' -exec grep -l rm {} +",
                    "ls &> /tmp/q", f"cat <<-EOF\n\tgit reset {HARD}\n\tEOF", f'echo "a <<EOF b"', "ls # rm -r q",
                    f"git commit -m \"$(cat <<'EOF'\nreset {HARD} and rm {RF} notes\nEOF\n)\""):
            self.assertIsNone(self.judge(cmd), cmd)
        self.assertEqual(self.judge(f'echo "a <<EOF b"\ngit reset {HARD}'), "ask")
        sys.path.insert(0, str(TW.parent))
        import tw
        t = time.time()
        tw.big_decision("echo " + "a" * 2_000_000)
        self.assertLess(time.time() - t, 3)

    def run_corpus(self, **env):
        here = SCRIPTS
        r = subprocess.run([sys.executable, str(here / "guard_corpus_run.py"), "--tw", str(TW), "--json"],
                           capture_output=True, text=True, env={**os.environ, **env})
        return r.returncode, json.loads(r.stdout)

    def test_adversarial_corpus(self):
        """T51: every bypass spelling found in review rounds, fed to the real hook; guard changes regress against it."""
        corpus = json.loads((SCRIPTS / "guard_corpus.json").read_text(encoding="utf-8"))
        cases = corpus["cases"]
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        pinned = {f"bg-{i:03d}" for i in range(1, 143)} | {f"sens-{i:03d}" for i in range(1, 20)}
        self.assertLessEqual(pinned, set(ids))  # ids only grow; none is dropped or reused
        self.assertTrue(all(case["expect"] in {"ask", "deny", "allow"} for case in cases))
        self.assertTrue(all(case.get("policy") in {None, *corpus["policies"]} for case in cases))
        self.assertEqual(len({c["cmd"] for c in cases if not c.get("policy")}), len([c for c in cases if not c.get("policy")]))
        rc, out = self.run_corpus()
        self.assertEqual(out["cases"], len(cases))
        for item in out["results"]:
            with self.subTest(id=item["id"]):
                self.assertTrue(item["pass"], item)
        self.assertEqual((rc, out["failed"]), (0, 0))

    def test_corpus_runner_fails_when_the_guard_crashes(self):
        """A crash prints nothing, like "allow": the runner reads the guard's error log so allow cases fail too."""
        rc, out = self.run_corpus(TW_TEST_GUARD_FAULT="boom", TW_CORPUS_KEEP_FAULT="1")
        self.assertEqual((rc, out["failed"]), (1, out["cases"]))
        rc, out = self.run_corpus(TW_TEST_GUARD_FAULT="boom")  # a stray fault in the caller's env is scrubbed
        self.assertEqual((rc, out["failed"]), (0, 0))

    def test_other_tools_and_bad_input_pass(self):
        for raw in ("[]", '"x"', '{"tool_input": "x"}', '{"tool_input": {"command": ["a"]}}', "", "null",
                    '{"tool_input": {"command": null}}'):
            r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=raw, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=b"\xff\xfe{", capture_output=True)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input="not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run([sys.executable, str(TW), "bash-guard"], input=json.dumps(
            {"tool_name": "Edit", "tool_input": {"command": "sudo x"}}), capture_output=True, text=True)
        self.assertEqual(r.stdout, "")
        self.assertIsNone(self.judge('git commit -m "unbalanced'))

class T79NoNestedDesktopThreads(TwCase):
    def _fixture(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8").splitlines()[0])

    def _guard(self, script, event):
        return subprocess.run([sys.executable, str(script), "bash-guard"], input=json.dumps(event),
                              capture_output=True, text=True, cwd=self.dir,
                              env={**os.environ, "TW_GUARD_LOG": str(self.dir / "guard.log")})

    def test_tool_name_is_normalized_for_start_and_archive_routes(self):
        start = self._fixture("t79_hook_start_session.jsonl")
        start["cwd"] = str(self.dir)
        state = self.state()
        state["lead"]["session_id"] = "session_live_lead"
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        variants = (" mcp__ccd_session__start_session", "MCP__CCD_SESSION__START_SESSION")
        for tool_name in variants:
            with self.subTest(tool_name=tool_name):
                start["tool_name"] = tool_name
                decision = json.loads(self._guard(TW, start).stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "deny")
        archive = {"cwd": str(self.dir), "session_id": "caller",
                   "tool_input": {"session_id": "session_live_lead"}}
        variants = (" mcp__claude-code-remote__archive_session",
                    "MCP__CLAUDE-CODE-REMOTE__ARCHIVE_SESSION")
        for tool_name in variants:
            with self.subTest(tool_name=tool_name):
                archive["tool_name"] = tool_name
                decision = json.loads(self._guard(TW, archive).stdout)["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecision"], "deny")

    def test_real_start_session_shape_denied_only_inside_teamwork_project(self):
        event = self._fixture("t79_hook_start_session.jsonl")
        event["cwd"] = str(self.dir)
        decision = json.loads(self._guard(TW, event).stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("create_session", decision["permissionDecisionReason"])
        self.assertIn("Remote Control", decision["permissionDecisionReason"])
        outside = self.dir.parent / (self.dir.name + "-outside")
        outside.mkdir()
        event["cwd"] = str(outside)
        self.assertEqual(self._guard(TW, event).stdout, "")

    def test_real_create_session_shape_is_unaffected(self):
        event = self._fixture("t79_hook_create_session.jsonl")
        event["cwd"] = str(self.dir)
        self.assertEqual(self._guard(TW, event).stdout, "")

    def test_nested_metadata_report_matches_lead_and_skips_archived(self):
        import copy
        rows = [json.loads(line) for line in (FIXTURES / "t79_nested_desktop_sessions.jsonl").read_text().splitlines()]
        child, parent = rows
        child["cwd"] = str(self.dir / "nested")
        parent["cwd"] = str(self.dir)
        parent["bridgeSessionIds"] = ["lead-remote"]
        state = self.state()
        state["lead"]["session_id"] = "session_lead-remote"
        metadata = self.dir / "metadata" / "account" / "org"
        metadata.mkdir(parents=True)
        (self.dir / "nested").mkdir()
        (metadata / "local_child.json").write_text(json.dumps(child), encoding="utf-8")
        (metadata / "local_parent.json").write_text(json.dumps(parent), encoding="utf-8")
        os.environ["TEAMWORK_CLAUDE_SESSIONS_DIR"] = str(self.dir / "metadata")
        try:
            found, gaps = tw._nested_session_report(self.dir / ".teamwork", state)
            self.assertEqual((gaps, [row["parent_is_lead"] for row in found]), (0, [True]))
            (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            ready_env = {**os.environ, "TEAMWORK_DIR": str(self.dir / ".teamwork"),
                         "TEAMWORK_CLAUDE_SESSIONS_DIR": str(self.dir / "metadata")}
            ready = subprocess.run([sys.executable, str(TW), "ready"], capture_output=True, text=True,
                                   cwd=self.dir, env=ready_env)
            self.assertIn("local_00000000-0000-4000-8000-000000000001", ready.stderr)
            self.assertIn("别归档 lead", ready.stderr)
            other_parent = dict(parent, sessionId="local_other", bridgeSessionIds=["other-remote"])
            other_child = dict(child, sessionId="local_other_child", spawnedFrom={"sessionId": "local_other"})
            archived = dict(child, sessionId="local_archived", isArchived=True)
            for name, value in (("local_other.json", other_parent), ("local_other_child.json", other_child),
                                ("local_archived.json", archived)):
                (metadata / name).write_text(json.dumps(value), encoding="utf-8")
            found, _ = tw._nested_session_report(self.dir / ".teamwork", state)
            self.assertEqual({row["session_id"] for row in found}, {child["sessionId"], "local_other_child"})
            self.assertEqual({row["parent_is_lead"] for row in found}, {True, False})
            state["lead"]["session_id"] = "session_unrelated"
            (self.dir / ".teamwork" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            ready = subprocess.run([sys.executable, str(TW), "ready"], capture_output=True, text=True,
                                   cwd=self.dir, env=ready_env)
            self.assertIn("local_00000000-0000-4000-8000-000000000001", ready.stderr)
            self.assertNotIn("别归档 lead", ready.stderr)
            (metadata / "local_broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(tw._nested_session_report(self.dir / ".teamwork", state)[1], 1)
        finally:
            os.environ.pop("TEAMWORK_CLAUDE_SESSIONS_DIR", None)

    def test_install_updates_legacy_matcher(self):
        sys.path.insert(0, str(SCRIPTS))
        import install
        (self.dir / "tmpd").mkdir()
        project = self.dir / "project"
        cfg = self.dir / "cfg"
        settings = project / ".claude" / "settings.local.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"hooks": {"PreToolUse": [{
            "matcher": "Bash,mcp__claude-code-remote__archive_session",
            "hooks": [{"type": "command", "command": f'"{sys.executable}" "{cfg / "skills/teamwork/scripts/tw.py"}" bash-guard || true'}]}]}}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--link", "--apply",
                                 "--config-dir", str(cfg), "--project", str(project)],
                                capture_output=True, text=True, env={**os.environ, "TMPDIR": str(self.dir / "tmpd"),
                                                                       "CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(result.returncode, 0, result.stderr)
        hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        matchers = [group["matcher"] for group in hooks]
        self.assertIn(install.BASH_GUARD_MATCHER, matchers)
        self.assertNotIn("Bash,mcp__claude-code-remote__archive_session", matchers)


if __name__ == "__main__":
    unittest.main()
