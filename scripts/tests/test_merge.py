try:
    from ._base import *
except ImportError:
    from _base import *


class Leases(TwCase):
    """T11: a ready task that overlaps an active task's paths is held back."""

    def setUp(self):
        super().setUp()
        for t in ("ui", "api"):
            self.tw("thread", "add", t)
        self.tw("plan", "approve", "--user-said", "ok")

    def ready(self):
        return json.loads(self.tw("ready").stdout)

    def test_declared_overlap_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/login.js,api/x.py")
        self.tw("task", "add", "T3", "--owner", "api", "--touches", "api/**")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["web/**"])
        self.assertIn("T3", r["ready"])
        self.assertNotIn("T2", r["ready"])
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", self.ready()["ready"])

    def test_same_owner_not_blocked_and_no_touches_listed(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/**")
        self.tw("task", "add", "T2", "--owner", "ui", "--touches", "web/a.js")
        self.tw("task", "add", "T3", "--owner", "api")
        self.tw("task", "set", "T1", "--status", "working")
        r = self.ready()
        self.assertEqual(r["blocked_by_lease"], {})
        self.assertEqual(r["no_touches"], ["T3"])

    def test_overlap_rules(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        self.assertTrue(tw.paths_overlap("web/", "web/sub/a.js"))
        self.assertTrue(tw.paths_overlap("web/*.js", "web/**"))
        self.assertTrue(tw.paths_overlap("./api/x.py", "api/x.py"))
        self.assertFalse(tw.paths_overlap("web/**", "api/x.py"))
        self.assertFalse(tw.paths_overlap("docs/a.md", "docs/b.md"))
        # review findings
        self.assertTrue(tw.paths_overlap("web", "web/app.js"))
        self.assertTrue(tw.paths_overlap("web", "web/"))
        self.assertTrue(tw.paths_overlap("web/**", "web"))
        self.assertFalse(tw.paths_overlap(".github/x", "github/x"))
        self.assertFalse(tw.paths_overlap("webapp/x", "web"))
        # round 2
        self.assertTrue(tw.paths_overlap("**/config.py", "config.py"))
        self.assertTrue(tw.paths_overlap("web/**/a.js", "web/a.js"))
        self.assertTrue(tw.paths_overlap(".", "api/x.py"))
        self.assertTrue(tw.paths_overlap("web/./a/../b.js", "web/b.js"))
        self.assertTrue(tw.paths_overlap("/web/", "web/b.js"))
        self.assertFalse(tw.paths_overlap("Web/a.js", "web/b.js"))
        # round 3: glob against glob
        for a, b in (("**/*.py", "api/"), ("**/*.py", "api"), ("api/**/*.py", "**/x.py"), ("*.py", "web/*"),
                     ("*/config.py", "api/*.py"), ("docs/*.md", "*/README.md"), ("web/{a,b}.py", "web/a.py"),
                     ("Web/App.py", "web/app.py"), ("app/[id]/page.js", "app/[id]/")):
            self.assertTrue(tw.paths_overlap(a, b), (a, b))
        self.assertTrue(tw.paths_overlap("web/*", "web/sub/a.js"))  # 'web/*' may be a folder
        self.assertTrue(tw.paths_overlap("{a,b}" * 20 + "/x", "b" * 20 + "/x"))  # too many braces: widened
        self.assertFalse(tw.paths_overlap("web/*.css", "web/a.py"))
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "../x", ok=False).returncode, 0)

    def test_round8_brace_widening(self):
        import importlib.util
        import time
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        many = "src/{" + ",".join(f"a{i}" for i in range(70)) + ",deep/q}.py"
        self.assertTrue(tw.paths_overlap(many, "src/deep/q.py"))  # widened across folders
        self.assertTrue(tw.paths_overlap("x/{z,{" + ",".join(f"a{i}" for i in range(70)) + "}}", "x/a5"))
        t0 = time.time()
        self.assertTrue(tw.paths_overlap("{a,b}" * 24 + "/x", "a" * 24 + "/x"))
        self.assertLess(time.time() - t0, 2)

    def test_two_ready_tasks_do_not_collide(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--touches", "web/app.js")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/**")
        r = self.ready()
        self.assertEqual(r["ready"], ["T1"])
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["with"], "T1")

class LeasesGit(GitCase):
    """T11: the real diff of an active branch counts, even when undeclared."""
    def test_branch_diff_blocks(self):
        self.tw("task", "add", "T1", "--owner", "ui")  # declared nothing
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("ui touches shared")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])


class LeasesGitReview(GitCase):
    """T11 review findings: base only on origin / wrong base, done-but-unmerged keeps its lease."""

    test_branch_diff_blocks = None

    def ui_branch_changes_shared(self):
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: ui touches shared")
        self.git("checkout", "-q", "main")

    def test_wrong_base_warns(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        r = json.loads(self.tw("ready").stdout)
        self.assertTrue(any("base branch master not found" in w for w in r["lease_warnings"]))

    def test_done_but_unmerged_still_holds(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")  # bare folder
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["status"], "done")
        self.git("merge", "-q", "tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])


    def test_round8_quoted_file_names(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "dir/")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "dir").mkdir()
        for n in ('a"b.py', "c\td.py", "e\\f.py"):
            (self.proj / "dir" / n).write_text("x\n")
        self.commit("T1: odd names")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(r["blocked_by_lease"]["T2"][0]["paths"]),
                         sorted(["dir/a\"b.py", "dir/c\td.py", "dir/e\\f.py"]))

    def test_round10_brace_cli_and_subfolder_project(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "web/{zorblax,quixbar}.js, api/x.py")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        self.assertEqual(st["tasks"]["T2"]["touches"], ["web/{zorblax,quixbar}.js", "api/x.py"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "{a,../x}", ok=False).returncode, 0)
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "web").mkdir()
        (self.proj / "web" / "quixbar.js").write_text("x\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        # a project in a subfolder of the repo: --touches are relative to the project folder
        sub = self.proj / "pkg"
        sub.mkdir()
        run = lambda *x: subprocess.run([sys.executable, str(TW), "-C", str(sub), *x], cwd=sub,
                                        capture_output=True, text=True)
        self.assertEqual(run("init", "pkg").returncode, 0)
        for x in (("thread", "add", "ui"), ("thread", "add", "api"), ("plan", "approve", "--user-said", "ok"),
                  ("task", "add", "T1", "--owner", "ui"), ("task", "add", "T2", "--owner", "api",
                                                            "--touches", "src/zorblax.ts")):
            self.assertEqual(run(*x).returncode, 0, x)
        for x in ("approved", "dispatched"):
            run("task", "set", "T1", "--status", x)
        self.git("checkout", "-q", "-b", "tw/pkg/ui")
        (sub / "src").mkdir()
        (sub / "src" / "zorblax.ts").write_text("x\n")
        self.commit("T1: zorblax")
        self.git("checkout", "-q", "main")
        r = json.loads(run("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["src/zorblax.ts"])

    def test_round11_earlier_task_squash_not_held_by_later_ones(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        (self.proj / "shared" / "config.py").write_text("x = 9\n")
        self.commit("T5: later edit on main")
        self.tw("task", "set", "T4", "--status", "working")  # the same branch goes on with T4
        r = json.loads(self.tw("ready").stdout)
        self.assertNotIn("T2", r["blocked_by_lease"], r)
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/{a,b.js, api/x.py", ok=False).returncode, 0)

    def test_round12_stale_origin_copy_after_squash(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        self.commit("T1: wip")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP, now behind
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.commit("T1: final")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.assertNotEqual(self.tw("task", "add", "T9", "--touches", "web/}a,b{.js, api/x.py", ok=False).returncode, 0)

    def test_round13_diverged_stale_copy_and_deleted_branch(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "shared").mkdir()
        (self.proj / "shared" / "config.py").write_text("x = 0\n")
        commit_at("T1: wip", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed WIP
        (self.proj / "shared" / "config.py").write_text("x = 1\n")
        self.git("add", "-A")
        subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--amend", "-m", "T1: final"], check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": "2030-01-01T11:00:00Z"})  # diverged from origin
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        commit_at("T1: zorblax (#12)", "2030-01-01T12:00:00Z")
        # T25 / T11-b: diverged copies both hold (work on either side is never dropped), so the stale pushed WIP
        # still holds after the amended copy landed, until it is pushed over, deleted or pruned
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        self.git("branch", "-q", "-D", "tw/proj/ui")  # the host deleted the branch: only the stale origin copy left
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # unsure: held ...
        self.git("update-ref", "-d", "refs/remotes/origin/tw/proj/ui")  # ... until `git fetch --prune`
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_t25_past_owners_branch_still_holds(self):
        """T11-a: a task handed to another thread keeps the lease of what the earlier owner left unmerged."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--owner", "db")  # db has no branch yet; ui's work is still unmerged
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])

    def test_t29_past_owner_holds_only_this_tasks_commits(self):
        """T29: after a handover, the earlier owner's branch holds only what its commits for this task changed;
        that thread's other work on the same branch is not counted against its own next task."""
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T5", "--owner", "ui")
        self.tw("task", "add", "T6", "--owner", "ui", "--touches", "zorb/quixbar.py")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.ui_branch_changes_shared()  # T1: shared/config.py
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        self.commit("T5: zorblax on the same branch")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T2"][0]["paths"], ["shared/config.py"])  # T1's own work still holds
        self.assertIn("T6", r["ready"], r)  # ui's other work is not held by db's T1
        self.assertNotIn("T6", r["blocked_by_lease"])

    def test_t29_untagged_and_merge_commits_still_hold(self):
        """T29 review: a `WIP:` commit (RECALL), a fixup! and a merge on the earlier owner's branch keep holding;
        a T2 commit is not T29's (id prefix)."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T2"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "w/zorblax.py, w/fix.py, m/side.py")
        self.tw("task", "add", "T8", "--owner", "ui", "--touches", "w/two.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "m").mkdir()
        (self.proj / "m" / "side.py").write_text("s = 1\n")
        self.commit("side work")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "w").mkdir()
        (self.proj / "w" / "zorblax.py").write_text("z = 1\n")
        self.commit("WIP: recall, half of the Zorblax parser")
        (self.proj / "w" / "fix.py").write_text("f = 1\n")
        self.commit("fixup! T29: quixbar")
        (self.proj / "w" / "two.py").write_text("t = 1\n")
        self.commit("T2: other work")
        self.git("merge", "-q", "--no-ff", "-m", "T29: merge side", "side")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        held = sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"])
        self.assertEqual(held, ["m/side.py", "w/fix.py", "w/zorblax.py"], r)
        self.assertIn("T8", r["ready"], r)  # T2's file is not held by T29

    def test_t29_other_lead_naming_this_task_and_renames_hold(self):
        """T29 review r2: `T5 and T29: ...` still names T29; a file T29 made that a T5 commit renamed still holds."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "q/both.py, q/new.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "q").mkdir()
        (self.proj / "q" / "both.py").write_text("b = 1\n")
        self.commit("T5 and T29: shared Quixbar")
        (self.proj / "q" / "old.py").write_text("o = 1\n" * 20)
        self.commit("T29: zorblax module")
        self.git("mv", "q/old.py", "q/new.py")
        self.commit("T5: rename")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]), ["q/both.py", "q/new.py"])

    def test_t29_tagged_merge_and_near_names_hold(self):
        """T29 review r3: a merge titled `T5: ...` (evil edits included) and `T5: T29-related` still hold."""
        self.tw("thread", "add", "db")
        for t in ("T29", "T5"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("task", "add", "T7", "--owner", "api", "--touches", "e/evil.py, e/side.py, e/near.py")
        self.tw("task", "set", "T29", "--status", "working")
        self.git("checkout", "-q", "-b", "side")
        (self.proj / "e").mkdir()
        (self.proj / "e" / "side.py").write_text("s = 1\n")
        self.commit("T5: side")
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        self.git("merge", "-q", "--no-ff", "--no-commit", "side")
        (self.proj / "e" / "evil.py").write_text("e = 1\n")
        self.commit("T5: merge side")
        (self.proj / "e" / "near.py").write_text("n = 1\n")
        self.commit("T5: T29-related Zorblax")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T29", "--owner", "db")
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(sorted(p for b in r["blocked_by_lease"]["T7"] for p in b["paths"]),
                         ["e/evil.py", "e/near.py", "e/side.py"])

    def test_t29_many_tasks_stay_fast(self):
        """T29 review r2: the branch log is parsed once, not once per task with a regex per task id."""
        import time
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T1", "--owner", "ui")
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        base = st["tasks"]["T1"]
        for i in range(600):
            st["tasks"][f"T{100 + i}"] = {**base, "owner": "db", "past_owners": ["ui"], "status": "working"}
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "z").mkdir()
        for i in range(40):
            (self.proj / "z" / f"f{i}.py").write_text(f"x = {i}\n")
            self.commit(f"T{100 + i}: zorblax {i}")
        self.git("checkout", "-q", "main")
        sys.path.insert(0, str(SCRIPTS))
        import tw
        t0 = time.time()
        held = tw._held_paths(json.loads((self.proj / ".teamwork" / "state.json").read_text()))
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(held["T105"], ["z/f5.py"])

    def test_t25_diverged_copies_both_hold(self):
        """T11-b: local and origin diverged with different work on each side: both sides count, not only the newer."""
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "zorb/quixbar.py")
        self.tw("task", "set", "T1", "--status", "working")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "zorb").mkdir()
        (self.proj / "zorb" / "quixbar.py").write_text("q = 1\n")
        commit_at("T1: pushed from the other seat", "2030-01-01T10:00:00Z")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")
        self.git("reset", "-q", "--hard", "main")
        (self.proj / "zorb").mkdir(exist_ok=True)
        (self.proj / "zorb" / "lanjing.py").write_text("l = 1\n")
        commit_at("T1: newer local work", "2030-01-01T11:00:00Z")
        self.git("checkout", "-q", "main")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T2", r["blocked_by_lease"], r)  # the older origin copy's file still holds

    def test_round14_unpushed_work_after_older_copy_squashed(self):
        def commit_at(msg, when):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-qm", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        for fix_id in ("T4", "T1"):  # a later task's work, or the done task's own review fix, not pushed
            self.tearDown()
            self.setUp()
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text("a = 1\n")
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "HEAD")  # pushed
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            (self.proj / "shared" / "zorb.py").write_text("a = 2\n")
            commit_at(f"{fix_id}: more on zorb", "2030-01-01T11:30:00Z")  # local only
            self.git("checkout", "-q", "main")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if fix_id == "T4":
                self.tw("task", "set", "T4", "--status", "working")
            self.git("merge", "-q", "--squash", pushed)
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"], fix_id)

    def test_round15_combined_squash_and_amended_fix(self):
        def commit_at(msg, when, *extra):
            self.git("add", "-A")
            subprocess.run(["git", "-C", str(self.proj), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "commit", "-q", *extra, "-m", msg], check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})
        lines = lambda *x: "".join(f"{v}\n" for v in x)
        for case in ("plain", "later-task", "amended"):
            self.tearDown()
            self.setUp()
            (self.proj / "shared").mkdir()
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, 7))
            commit_at("base file", "2030-01-01T09:00:00Z")
            self.tw("task", "add", "T1", "--owner", "ui")
            self.tw("task", "add", "T4", "--owner", "ui", "--touches", "zz/q.js")
            self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/zorb.py")
            self.git("checkout", "-q", "-b", "tw/proj/ui")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, 7))
            commit_at("T1: zorblax", "2030-01-01T10:00:00Z")
            pushed = self.git("rev-parse", "HEAD").stdout.strip()
            self.git("update-ref", "refs/remotes/origin/tw/proj/ui", pushed)
            if case == "later-task":
                (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, "four", 5, 6, 7))
                commit_at("T4: quixbar", "2030-01-01T11:00:00Z")
            if case == "amended":
                (self.proj / "shared" / "zorb.py").write_text(lines("ONE", 2, 3, 4, 5, 6, 7))
                commit_at("T1: zorblax", "2030-01-01T11:00:00Z", "--amend")
            self.git("checkout", "-q", "main")
            (self.proj / "shared" / "zorb.py").write_text(lines(1, 2, 3, 4, 5, 6, "seven"))
            commit_at("lead: line 7", "2030-01-01T10:30:00Z")
            self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
            if case == "later-task":
                self.tw("task", "set", "T4", "--status", "working")
            (self.proj / "shared" / "zorb.py").write_text(lines("one", 2, 3, 4, 5, 6, "seven"))  # host squash
            commit_at("T1: Zorblax (#3)", "2030-01-01T12:00:00Z")
            r = json.loads(self.tw("ready").stdout)
            if case == "plain":
                self.assertIn("T2", r["ready"], case)  # the squash folded in the base's own edit
            else:
                self.assertIn("T2", r["blocked_by_lease"], case)  # later work / an unpushed fix is not on the base

    def test_round2_stale_origin_rename_recalled(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("update-ref", "refs/remotes/origin/main", "main")  # origin/main from before the merge
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "tw/proj/ui")  # merged locally, not pushed: local main is newer
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        # a rename moves files out of a leased folder: both names count
        self.git("checkout", "-q", "-b", "tw/proj/api2", "main")
        self.tw("thread", "add", "api2")
        self.tw("task", "add", "T3", "--owner", "api2")
        self.tw("task", "add", "T4", "--owner", "ui", "--touches", "shared/**")
        (self.proj / "moved.py").write_text((self.proj / "shared" / "config.py").read_text())
        (self.proj / "shared" / "config.py").unlink()
        self.commit("T3: move config")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T3", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("shared/config.py", r["blocked_by_lease"]["T4"][0]["paths"])
        self.tw("task", "set", "T3", "--status", "recalled")  # WIP stays on the branch
        r = json.loads(self.tw("ready").stdout)
        self.assertEqual(r["blocked_by_lease"]["T4"][0]["status"], "recalled")

    def test_round3_squash_merge_releases(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])

    def test_round8_real_files_and_later_fix(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tw", TW)
        tw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tw)
        F = tw.RealFile
        for g, f in (("*.css", "zorb/app.py"), ("**/*.test.js", "quix/main.go"), ("src/**/*.ts", "src/app.py"),
                     ("*.md", "README"), ("zorb/*.py", "zorb/back\\sl.txt")):
            self.assertFalse(tw.paths_overlap(g, F(f)), (g, f))
        for g, f in (("**/*.py", "a/b.py"), ("zorb/", "zorb/x/y.py"), ("zorb", "zorb/a.py"), ("*.md", "d/x.md"),
                     ("zorb/*.py", "zorb/back\\sl.py"), ("a/[*]b", "a/*b")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
        self.assertTrue(tw.paths_overlap(F("Zorb/A.py"), F("zorb/a.py")))
        for g, f in (("app/[id]/page.tsx", "app/[id]/page.tsx"), ("app/[id]/", "app/[id]/x.ts"),
                     ("src/routes/[slug]/**", "src/routes/[slug]/+page.svelte"), ("{a,b}.py", "{a,b}.py"),
                     ("*blax", "src/zorblax/gen.py")):
            self.assertTrue(tw.paths_overlap(g, F(f)), (g, f))
            self.assertTrue(tw.paths_overlap(g, f), (g, f))  # declared and real agree
        # squash-merged, then a review fix on the branch that is not merged: still held
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")
        self.commit("T1: review fix")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round4_squash_then_base_edits_and_topic_blocked(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        self.git("merge", "-q", "--squash", "tw/proj/ui")
        self.commit("T1: squash of tw/proj/ui")
        (self.proj / "shared" / "config.py").write_text("x = 2\n")  # main edits the file afterwards
        self.commit("later edit")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["ready"])
        self.tw("task", "add", "T3", "--owner", "ui", "--topics", "t", "--touches", "z.py")
        self.tw("task", "add", "T4", "--owner", "api", "--topics", "t")
        self.tw("thread", "add", "db")
        self.tw("task", "add", "T5", "--owner", "db", "--touches", "z.py")
        self.tw("task", "set", "T4", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("T3", r["blocked_by_topic"])
        self.assertIn("T5", r["ready"])  # T3 will not start, so it holds no lease

    def test_round5_mention_is_not_a_merge(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "shared/config.py")
        self.ui_branch_changes_shared()
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "test", "--user-said", "ok")
        (self.proj / "NOTES").write_text("n\n")
        self.commit("plan: T1 review pending, do not merge yet")  # names T1, touches nothing of it
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])
        (self.proj / "shared").mkdir(exist_ok=True)
        (self.proj / "shared" / "config.py").write_text("other\n")
        self.commit("Revert T1 partially")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

    def test_round5_one_pass_no_collisions(self):
        for n in ("web", "db"):
            self.tw("thread", "add", n)
        self.tw("task", "add", "X", "--owner", "api", "--touches", "a/")
        self.tw("task", "add", "A", "--owner", "ui", "--topics", "t", "--touches", "a/")
        self.tw("task", "add", "B", "--owner", "web", "--topics", "t", "--touches", "b/")
        self.tw("task", "add", "C", "--owner", "db", "--touches", "b/x.py")
        self.tw("task", "set", "X", "--status", "working")
        r = json.loads(self.tw("ready").stdout)
        self.assertIn("A", r["blocked_by_lease"])
        self.assertIn("B", r["ready"])
        self.assertIn("C", r["blocked_by_lease"])  # B is offered, so it holds b/

    def test_round6_bracket_names_and_mention_edits(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("task", "add", "T2", "--owner", "api", "--touches", "docs/*.md")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "docs").mkdir()
        (self.proj / "docs" / "notes[draft.md").write_text("d\n")
        self.commit("T1: odd name")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--status", "working")
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])  # no crash, still a clash
        self.tw("task", "set", "T1", "--status", "done", "--skip-review", "t", "--user-said", "ok")
        (self.proj / "docs").mkdir(exist_ok=True)
        (self.proj / "docs" / "notes[draft.md").write_text("prep\n")
        self.commit("Prepare docs for T1 (not merged yet)")  # names T1 and edits the file, but is not T1
        self.assertIn("T2", json.loads(self.tw("ready").stdout)["blocked_by_lease"])

class HandoverGit(GitCase):
    test_branch_diff_blocks = None

    def test_origin_commits_not_hidden_by_local_branch(self):
        self.git("checkout", "-q", "-b", "tmp")
        (self.proj / "w.txt").write_text("w\n")
        self.commit("T1: pushed work")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", sha)
        self.git("branch", "tw/proj/ui", "main")  # local branch has nothing
        out = self.tw("handover", "ui").stdout
        self.assertIn("T1: pushed work", out)
        self.assertIn("local and origin differ", out)
        self.assertIn("[origin/tw/proj/ui]", out)


    def test_round2_missing_base_keeps_configured_name(self):
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "nope"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        out = self.tw("handover", "ui").stdout
        self.assertIn("(base nope)", out)
        self.assertIn("base branch nope not found", out)

class Trace(GitCase):
    """T17: follow a request to tasks, threads, sessions, commits and report time."""

    test_branch_diff_blocks = None  # inherited fixture only

    def test_trace_request_end_to_end(self):
        self.tw("thread", "set", "ui", "--session", "sess-ui", "--status", "working")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("task", "add", "T2", "--owner", "ui", "--title", "other work")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "b.css").write_text("blue\n")
        self.commit("T1: blue button")
        (self.proj / "c.css").write_text("x\n")
        self.commit("T2: unrelated")
        self.git("checkout", "-q", "main")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        k = t["tasks"][0]
        self.assertEqual((k["task"], k["thread"], k["session_id"], k["branch"]), ("T1", "ui", "sess-ui", "tw/proj/ui"))
        self.assertEqual(len(k["commits"]), 1)  # only its own commits
        self.assertTrue(any("T2: unrelated" in c for c in t["other_branch_work"]["tw/proj/ui"]))
        self.git("merge", "-q", "--no-ff", "tw/proj/ui", "-m", "Merge ui (T1)")
        self.tw("task", "set", "T1", "--accept", "x", "--status", "done", "--review", "pass", "--reviewer", "rev")
        self.tw("req", "set", "R1", "--status", "reported")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["reported_at"])
        commits = t["tasks"][0]["commits"]
        self.assertTrue(any("T1: blue button" in c for c in commits))
        self.assertFalse(any("T2: unrelated" in c for c in commits))  # finished task: only its own commits
        evs = [e["ev"] for e in t["events"]]
        self.assertIn("req-add", evs)
        self.assertIn("task-set", evs)
        text = self.tw("trace", "R1").stdout
        self.assertIn("R1 [reported] make the button blue", text)
        self.assertIn("session sess-ui", text)

    def test_board_cross_references(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "blue button")
        self.tw("req", "add", "make the button blue", "--tasks", "T1")
        board = self.tw("board").stdout
        self.assertIn("**R1** [dispatched] make the button blue — T1", board)
        row = [ln for ln in board.splitlines() if ln.startswith("| T1 |")][0]
        self.assertTrue(row.rstrip().endswith("| R1 |"))

    def test_review_reassigned_task_keeps_commits(self):
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "x")
        self.tw("req", "add", "x", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "early.txt").write_text("e\n")
        self.commit("T1: early work by ui")
        self.git("checkout", "-q", "main")
        self.tw("task", "set", "T1", "--owner", "api")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["owners"], ["ui", "api"])
        self.assertTrue(any("T1: early work by ui" in c for c in t["tasks"][0]["commits"]))

    def test_review_bad_log_lines_wrong_base_zh_closed(self):
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        with open(self.proj / ".teamwork" / "log.jsonl", "ab") as f:
            f.write(b'[1,2]\n"str"\n42\n{bad\n\xff\xfe\n')
        st = json.loads((self.proj / ".teamwork" / "state.json").read_text())
        st["repo"]["base"] = "master"
        (self.proj / ".teamwork" / "state.json").write_text(json.dumps(st))
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertTrue(t["warnings"])
        self.tw("prefs", "--set", "language=zh")
        self.tw("req", "drop", "R1", "--user-said", "不做了")
        text = self.tw("trace", "R1").stdout
        self.assertIn("[已放弃]", text)
        self.assertIn("线程 ui", text)
        self.assertIn("关闭 20", text)
        self.assertIn("| R1✗ |", self.tw("board").stdout)

    def test_trace_without_git(self):
        subprocess.run(["rm", "-rf", str(self.proj / ".git")], check=True)
        self.tw("task", "add", "T1", "--owner", "ui")
        self.tw("req", "add", "x", "--tasks", "T1")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual(t["tasks"][0]["commits"], [])


    def test_round2_ids_dedupe_base_text(self):
        self.tw("thread", "set", "ui", "--session", "s1")
        self.tw("thread", "reseat", "ui", "--session", "s2", "--reason", "failed")
        for t in ("T1", "T2", "T3"):
            self.tw("task", "add", t, "--owner", "ui", "--title", t)
        self.tw("req", "add", "two tasks", "--tasks", "T1,T2")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: one", "T2: two", "T1.5: half step", "T3: other"):
            (self.proj / (msg[:4].replace(":", "").replace(".", "_") + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/remotes/origin/tw/proj/ui", "tw/proj/ui")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        t1 = t["tasks"][0]["commits"]
        self.assertEqual(len(t1), 1, t1)  # T1.5 is not T1
        other = [c for cs in t["other_branch_work"].values() for c in cs]
        self.assertEqual(sorted(c.split(" ", 2)[2] for c in other), ["T1.5: half step", "T3: other"])  # once each
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            f.write(json.dumps({"ev": "custom", "ids": "R1,R12"}) + "\n" + json.dumps({"ev": "x", "ids": "R12"}) + "\n")
        evs = [e["ev"] for e in json.loads(self.tw("trace", "R1", "--json").stdout)["events"]]
        self.assertIn("custom", evs)
        self.assertNotIn("x", evs)
        self.tw("req", "drop", "R1", "--user-said", "stop", "--note", "superseded by R2")
        text = self.tw("trace", "R1").stdout
        self.assertIn("superseded by R2", text)
        self.assertIn("sessions: s1 (failed) -> s2", text)

    def test_round3_sub_ids_and_bad_log_fields(self):
        for t in ("T1", "T1a"):
            self.tw("task", "add", t, "--owner", "ui")
        self.tw("req", "add", "one", "--tasks", "T1")
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        for msg in ("T1: main", "T1a: sub", "T1-2: part", "T1_x: other"):
            (self.proj / (str(abs(hash(msg))) + ".txt")).write_text(msg)
            self.commit(msg)
        self.git("checkout", "-q", "main")
        with open(self.proj / ".teamwork" / "log.jsonl", "a") as f:
            for e in ({"ids": 42}, {"task": "T1", "snap_task": "str"}, {"task": "T1", "owner": ["ui"]}):
                f.write(json.dumps(e) + "\n")
        t = json.loads(self.tw("trace", "R1", "--json").stdout)
        self.assertEqual([c.split(" ", 4)[4] for c in t["tasks"][0]["commits"]], ["T1: main"])

    def test_round2_init_detects_base_and_lead_base(self):
        self.git("branch", "-m", "main", "trunk")
        other = self.proj.parent / "p2"
        subprocess.run(["git", "clone", "-q", str(self.proj), str(other)], check=True, capture_output=True)
        subprocess.run([sys.executable, str(TW), "-C", str(other), "init", "p2", "--path", str(other)],
                       check=True, capture_output=True, cwd=other)
        self.assertEqual(json.loads((other / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")
        self.tw("lead", "--base", "trunk")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["repo"]["base"], "trunk")

class MergeRehearsal(GitCase):
    """T33: `merge` rehearses in a temp worktree; the live checkout only ever moves by fast-forward."""

    test_branch_diff_blocks = None

    def setUp(self):
        super().setUp()
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        (self.proj / "zorb.py").write_text("a = 1\n")
        (self.proj / "check.sh").write_text("grep -q 'a = ' zorb.py\n")
        self.commit("base")
        self.tw("task", "add", "T1", "--owner", "ui", "--title", "quixbar",
                "--primary", "The user sees one quixbar result")
        proof = self.proj / "merge-primary-proof.txt"
        proof.write_text("the real artifact displays 1 quixbar result", encoding="utf-8")
        self.tw("task", "verify", "T1", "--seen", "--how", "open the real quixbar artifact",
                "--evidence", str(proof))
        self.git("checkout", "-q", "-b", "tw/proj/ui")
        (self.proj / "quix.py").write_text("q = 1\n")
        self.commit("T1: quixbar")
        self.git("checkout", "-q", "main")
        self.main0 = self.head()

    def head(self, ref="main"):
        return self.git("rev-parse", ref).stdout.strip()

    def m(self, *x, ok=True):
        return self.tw("merge", "tw/proj/ui", "--sha", self.head("tw/proj/ui"), *x, ok=ok)

    def merge(self, *x, ok=True):
        return self.m("--test", "sh check.sh", *x, ok=ok)

    def test_merge_runs_shards_in_parallel_and_names_the_slow_one(self):
        self.git("checkout", "-q", "tw/proj/ui")
        shard_dir = self.proj / "scripts" / "tests"; shard_dir.mkdir(parents=True)
        (shard_dir / "test_fast_a.py").write_text("print('fast-a passed')\n")
        (shard_dir / "test_fast_b.py").write_text("print('fast-b passed')\n")
        (shard_dir / "test_slow.py").write_text("import time; time.sleep(30)\n")
        self.commit("T1: add merge check shards")
        self.git("checkout", "-q", "main")
        state_file = self.proj / ".teamwork" / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["prefs"].update(check_shards="scripts/tests/test_*.py", check_jobs=2,
                              check_shard_timeout_minutes=0.05)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        result = self.m("--test", "python3 {shard}", ok=False)
        print(result.stderr, end="")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["check_shards"], {"total": 3, "passed": 2,
                                                   "failed": [], "timed_out": ["scripts/tests/test_slow.py"]})
        self.assertIn("scripts/tests/test_slow.py", result.stderr)
        self.assertRegex(result.stderr, r"\[1/3 start")
        self.assertRegex(result.stderr, r"\[2/3 start")
        self.assertLess(result.stderr.index("[2/3 start"), result.stderr.index("[1/3 passed"))
        match = re.search(r"merge check log: (.+)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        log_file = Path(match.group(1).strip())
        self.assertIn("test_fast_a.py", log_file.read_text(encoding="utf-8"))
        outputs = Path(str(log_file)[:-4] + "-shards")
        self.assertIn("fast-a passed", (outputs / "001-scripts_tests_test_fast_a.py.out").read_text())
        self.assertIn("fast-b passed", (outputs / "002-scripts_tests_test_fast_b.py.out").read_text())

    def test_merge_timeouts_fall_back_for_negative_and_nonfinite_values(self):
        import tw
        for value in (-1, float("nan"), float("inf"), float("-inf"), "not-a-number"):
            self.assertEqual(tw._positive_timeout(value, 60), 60)
        self.assertEqual(tw._positive_timeout(0, 60), 0)
        self.assertEqual(tw._positive_timeout(0.05, 60), 0.05)

    def test_sigterm_stops_unsharded_detached_process_group(self):
        import errno, signal, time
        self.git("checkout", "-q", "tw/proj/ui")
        script = self.proj / "check.py"
        script.write_text(
            "import os,subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            "open(os.environ['TW_PIDFILE'],'w').write(f'{os.getpid()} {child.pid}')\n"
            "time.sleep(30)\n", encoding="utf-8")
        self.commit("T1: add unsharded signal check")
        self.git("checkout", "-q", "main")
        pidfile = self.proj.parent / "active-check-pids"
        env = dict(os.environ, TW_PIDFILE=str(pidfile))
        command = [sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                   self.head("tw/proj/ui"), "--test", f'"{sys.executable}" check.py']
        process = subprocess.Popen(command, cwd=self.proj, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not pidfile.exists(): time.sleep(.05)
        self.assertTrue(pidfile.exists(), "unsharded check did not start")
        pids = [int(value) for value in pidfile.read_text().split()]
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = []
            for pid in pids:
                try: os.kill(pid, 0); alive.append(pid)
                except OSError as exc:
                    if exc.errno != errno.ESRCH: raise
            if not alive: break
            time.sleep(.05)
        else: self.fail(f"check process group members survived merge SIGTERM: {alive}")

    def test_sigterm_stops_all_active_shard_processes(self):
        import signal, time
        import errno
        self.git("checkout", "-q", "tw/proj/ui")
        shard_dir=self.proj/"scripts"/"tests"; shard_dir.mkdir(parents=True)
        (shard_dir/"test_signal.py").write_text(
            "import os,time; open(os.environ['TW_PIDFILE'],'w').write(str(os.getpid())); time.sleep(30)\n")
        self.commit("T1: add signal check shard")
        self.git("checkout", "-q", "main")
        state_file=self.proj/".teamwork"/"state.json"
        state=json.loads(state_file.read_text(encoding="utf-8"))
        state["prefs"].update(check_shards="scripts/tests/test_signal.py",check_jobs=1)
        state_file.write_text(json.dumps(state),encoding="utf-8")
        pidfile=self.proj.parent/"active-shard.pid"
        env=dict(os.environ,TW_PIDFILE=str(pidfile))
        command=[sys.executable,str(TW),"-C",str(self.proj),"merge","tw/proj/ui","--sha",
                 self.head("tw/proj/ui"),"--test","python3 {shard}"]
        process=subprocess.Popen(command,cwd=self.proj,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not pidfile.exists(): time.sleep(.05)
        self.assertTrue(pidfile.exists(),"shard did not start")
        shard_pid=int(pidfile.read_text())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try: os.kill(shard_pid,0)
            except OSError as exc:
                if exc.errno==errno.ESRCH: break
                raise
            time.sleep(.05)
        else: self.fail(f"shard process {shard_pid} survived merge SIGTERM")

    def test_merge_without_shards_behaves_as_before(self):
        result = json.loads(self.merge().stdout)
        self.assertEqual(result["checks"], "pass")
        self.assertNotIn("check_shards", result)

    def no_temp_worktrees(self):
        self.assertNotIn("tw-merge-", self.git("worktree", "list").stdout)

    def test_rehearsal_changes_nothing(self):
        r = json.loads(self.merge().stdout)
        self.assertEqual((r["checks"], r["applied"]), ("pass", False))
        self.assertEqual(self.head(), self.main0)
        self.assertFalse((self.proj / "quix.py").exists())
        self.no_temp_worktrees()

    def test_apply_fast_forwards_live_checkout(self):
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])
        self.assertEqual(self.head(), r["merged"])
        self.assertTrue((self.proj / "quix.py").exists())  # the live files moved with the ref
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_conflict_leaves_live_untouched(self):
        (self.proj / "quix.py").write_text("q = 2\n")
        self.commit("T2: other quix")
        before = self.head()
        r = self.merge("--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("quix.py", r.stdout)
        self.assertEqual(self.head(), before)
        self.assertNotIn("<<<<<<<", (self.proj / "quix.py").read_text())
        self.no_temp_worktrees()

    def test_red_checks_leave_live_untouched(self):
        r = self.m("--test", "exit 3", "--apply", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checks failed", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()

    def test_needs_a_check_and_a_squash_subject(self):
        self.assertIn("no check to run", self.m(ok=False).stderr)
        self.assertIn("--squash needs --message", self.merge("--squash", ok=False).stderr)
        self.tw("prefs", "--set", "check_cmd=sh check.sh")
        r = json.loads(self.m("--squash", "--message", "T1: quixbar (squash)", "--apply").stdout)
        self.assertEqual(self.git("log", "-1", "--format=%s %P").stdout.split()[0:2], ["T1:", "quixbar"])
        self.assertEqual(len(self.git("log", "-1", "--format=%P").stdout.split()), 1)  # one parent: a squash
        self.assertTrue(r["applied"])
        r = json.loads(self.m("--squash", "--message", "T1: again", "--no-test", "docs only").stdout)
        self.assertIn("nothing to merge", r["note"])  # an empty squash is not a failure

    def test_dirty_live_base_refused(self):
        (self.proj / "zorb.py").write_text("a = 9\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("uncommitted changes", r.stderr)
        self.assertEqual(self.head(), self.main0)

    def test_base_not_checked_out_moves_ref(self):
        self.git("checkout", "-q", "-b", "elsewhere")
        r = json.loads(self.merge("--apply").stdout)
        self.assertEqual(self.head("main"), r["merged"])
        self.assertEqual(self.head("HEAD"), self.main0)  # the checkout on another branch did not move

    def test_only_the_reviewed_sha_merges(self):
        reviewed = self.head("tw/proj/ui")
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "late.py").write_text("z = 1\n")
        self.commit("T1: pushed after the review")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", reviewed[:9], "--test", "sh check.sh", "--apply", ok=False)
        self.assertIn("not the reviewed", r.stderr)
        self.assertIn("pushed after the review", json.loads(r.stdout)["extra_commits"][0])
        self.assertEqual(self.head(), self.main0)
        self.git("checkout", "-q", "-b", "stray")
        (self.proj / "stray.py").write_text("s = 1\n")
        self.commit("stray")
        self.git("checkout", "-q", "main")
        r = self.tw("merge", "tw/proj/ui", "--sha", self.head("stray"), "--test", "sh check.sh", ok=False)
        self.assertIn("not on tw/proj/ui", r.stderr)
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "abcdef0", "--no-test", "x",
                                                ok=False).stderr)
        for name in ("tw/proj/ui", "stray", "HEAD", f"{reviewed}~0", "main", " "):  # names never stand for a review
            r = self.tw("merge", "tw/proj/ui", "--sha", name, "--no-test", "x", ok=False)
            self.assertIn("commit id", r.stderr, name)
        self.git("branch", "deadbeef", "stray")  # a branch named like hex is still not that commit
        self.assertIn("no such commit", self.tw("merge", "tw/proj/ui", "--sha", "deadbeef", "--no-test", "x",
                                                ok=False).stderr)
        self.assertTrue(json.loads(self.tw("merge", "tw/proj/ui", "--sha", f" {self.head('tw/proj/ui').upper()} ",
                                           "--no-test", "x").stdout)["merged"])
        self.assertNotEqual(self.tw("merge", "tw/proj/ui", "--no-test", "x", ok=False).returncode, 0)  # --sha required

    def test_holder_switching_branch_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} checkout -q -b zorblax", ok=False)
        self.assertRegex(r.stderr, "changed meanwhile|no longer on main")
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.head("zorblax"), self.main0)  # the other branch was not fast-forwarded
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_base_checked_out_during_checks_refused(self):
        self.git("checkout", "-q", "-b", "side")
        r = self.merge_with_check(f"git -C {self.proj} checkout -q main", ok=False)
        self.assertIn("changed meanwhile", r.stderr)
        self.assertEqual(self.head(), self.main0)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no").stdout, "")

    def test_holder_changing_during_checks_refused(self):
        for cmd, why in ((f"echo junk > {self.proj / 'zorb.py'}", "uncommitted changes"),
                         (f"git -C {self.proj} commit -q --allow-empty -m moved", "moved to")):
            r = self.merge_with_check(cmd, ok=False)
            self.assertIn(why, r.stderr)
            self.assertFalse(json.loads(r.stdout)["applied"])
            self.git("checkout", "-q", "--", "zorb.py")
            self.no_temp_worktrees()
        self.assertNotIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())

    def test_rebase_started_during_checks_refused(self):
        r = self.merge_with_check(f"git -C {self.proj} rebase -q --exec false HEAD~1 >/dev/null 2>&1; true", ok=False)
        self.assertRegex(r.stderr, "no longer on main|changed meanwhile")
        self.git("rebase", "--abort")
        self.assertEqual(self.head(), self.main0)

    def test_ignored_local_file_in_the_way_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "quixbar.env").write_text("from the branch\n")
        self.git("add", "-f", "quixbar.env")
        self.commit("T1: env")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.env\n")
        (self.proj / "quixbar.env").write_text("MY LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertIn("quixbar.env", json.loads(r.stdout)["untracked_in_the_way"])
        self.assertEqual((self.proj / "quixbar.env").read_text(), "MY LOCAL\n")

    def test_sigterm_during_checks_cleans_up(self):
        flag = self.proj.parent / "started"
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", f"touch {flag}; sleep 30", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(300):
            if flag.exists():
                break
            time.sleep(0.05)
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertIn("nothing changed", err)
        self.assertEqual(self.head(), self.main0)
        self.no_temp_worktrees()
        self.assertEqual([x for x in Path(tempfile.gettempdir()).glob("tw-merge-*/pid")
                          if x.read_text() == str(p.pid)], [])  # its temp folder is gone too

    def test_sigterm_during_fast_forward_waits_for_it(self):
        flag = self.proj.parent / "ff-started"
        hook = self.proj / ".git" / "hooks" / "post-merge"
        hook.write_text(f'#!/bin/sh\ncase "$(pwd -P)" in "{self.proj.resolve()}") touch "{flag}"; sleep 2;; esac\n')
        hook.chmod(0o755)
        p = subprocess.Popen([sys.executable, str(TW), "-C", str(self.proj), "merge", "tw/proj/ui", "--sha",
                              self.head("tw/proj/ui"), "--test", "sh check.sh", "--apply"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(400):
            if flag.exists():
                break
            time.sleep(0.05)
        self.assertTrue(flag.exists())
        p.terminate()
        out, err = p.communicate(timeout=20)
        self.assertNotIn("nothing changed", err)
        self.assertTrue(json.loads(out)["applied"])  # the report is printed before the waiting signal acts
        self.assertNotEqual(self.head(), self.main0)
        self.assertTrue((self.proj / "quix.py").exists())
        self.assertIn('"ev": "merge"', (self.proj / ".teamwork" / "log.jsonl").read_text())
        self.no_temp_worktrees()

    def test_t42_folder_and_file_prefix_clashes_refused(self):
        """T42: a local ignored folder where the merge adds a file, and a local ignored file where it adds a folder."""
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "bld").write_text("tracked file\n")
        (self.proj / "cfg").mkdir()
        (self.proj / "cfg" / "a").write_text("tracked\n")
        self.git("add", "-f", "bld", "cfg/a")
        self.commit("T1: bld and cfg")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("bld\ncfg\n")
        (self.proj / "bld").mkdir()
        (self.proj / "bld" / "x").write_text("LOCAL\n")
        (self.proj / "cfg").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(sorted(json.loads(r.stdout)["untracked_in_the_way"]), ["bld/x", "cfg"])
        self.assertEqual(((self.proj / "bld" / "x").read_text(), (self.proj / "cfg").read_text()), ("LOCAL\n",) * 2)

    def test_t42_non_ascii_paths_in_the_way(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "中文").write_text("tracked\n")
        self.commit("T1: zh")
        self.git("checkout", "-q", "main")
        (self.proj / "中文").mkdir()
        (self.proj / "中文" / "k").write_text("LOCAL\n")
        r = self.merge("--apply", ok=False)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["中文/k"])
        self.assertEqual(self.head(), self.main0)

    def test_t42_ignored_file_appearing_during_checks_refused(self):
        self.git("checkout", "-q", "tw/proj/ui")
        (self.proj / "k.secret").write_text("from the branch\n")
        self.git("add", "-f", "k.secret")
        self.commit("T1: secret")
        self.git("checkout", "-q", "main")
        (self.proj / ".git" / "info" / "exclude").write_text("*.secret\nrace.sh\n")
        r = self.merge_with_check(f"echo LOCAL > {self.proj / 'k.secret'}", ok=False)
        self.assertIn("appeared meanwhile", r.stderr)
        self.assertEqual((self.proj / "k.secret").read_text(), "LOCAL\n")
        self.assertEqual(self.head(), self.main0)

    def test_t42_check_timeout_and_no_stdin(self):
        stf = self.proj / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        st["prefs"]["check_timeout_minutes"] = 0.02  # about a second
        stf.write_text(json.dumps(st))
        r = self.m("--test", "sleep 30", ok=False)
        self.assertIn("were stopped", r.stderr)
        self.no_temp_worktrees()
        self.assertEqual(json.loads(self.m("--test", "cat").stdout)["checks"], "pass")  # stdin is empty, not ours
        self.assertNotEqual(self.tw("prefs", "--set", "check_timeout_minutes=x", ok=False).returncode, 0)

    def test_t42_prunable_base_worktree_is_nobody(self):
        self.git("checkout", "-q", "-b", "zorb-side")
        side = Path(tempfile.mkdtemp()) / "m"
        self.git("worktree", "add", "-q", str(side), "main")
        shutil.rmtree(side)  # its folder is gone: the worktree is prunable
        r = json.loads(self.merge("--apply").stdout)
        self.assertTrue(r["applied"])  # the first run, not after a misleading "changed meanwhile"
        self.assertEqual(self.head(), r["merged"])

    def test_t42_live_or_young_leftovers_are_kept(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("1")  # alive, not ours to signal: PermissionError means running
        young = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(young / "wt"), "main")  # no pid file, just made
        self.merge()
        self.assertTrue((tmp / "wt").exists() and (young / "wt").exists())
        for w in (tmp, young):
            self.git("worktree", "remove", "--force", str(w / "wt"))

    def test_killed_run_leftover_is_cleaned_next_time(self):
        tmp = Path(tempfile.mkdtemp(prefix="tw-merge-"))
        self.git("worktree", "add", "-q", "--detach", str(tmp / "wt"), "main")
        (tmp / "pid").write_text("999999999")  # no such process
        self.merge()
        self.assertFalse(tmp.exists())
        self.no_temp_worktrees()

    def merge_with_check(self, cmd, ok=True):
        (self.proj / "race.sh").write_text(cmd + "\n")  # untracked: not in the rehearsal, so call it by path
        return self.m("--test", f"sh {self.proj / 'race.sh'}", "--apply", ok=ok)

    def test_check_cmd_is_text_and_no_test_wins(self):
        self.tw("prefs", "--set", "check_cmd=true")
        self.assertEqual(json.loads((self.proj / ".teamwork" / "state.json").read_text())["prefs"]["check_cmd"], "true")
        self.tw("prefs", "--set", "check_cmd=exit 5")
        r = json.loads(self.m("--no-test", "docs only").stdout)
        self.assertEqual(r["checks"], "none (docs only)")

    def test_untracked_file_in_the_way_refused_before_checks(self):
        (self.proj / "quix.py").write_text("mine\n")
        r = self.m("--test", "exit 7", "--apply", ok=False)
        self.assertIn("untracked files", r.stderr)
        self.assertEqual(json.loads(r.stdout)["untracked_in_the_way"], ["quix.py"])
        self.assertEqual((self.proj / "quix.py").read_text(), "mine\n")
        self.assertEqual(self.head(), self.main0)

class ThreadBranchAndPrivateScratch(TwCase):
    """T32: `thread set --branch` no longer exits 0 while changing nothing; private scratch has a home."""

    def branch(self, t="db"):
        return self.state()["threads"][t]["branch"]

    def test_set_branch_while_planned(self):
        self.tw("thread", "add", "db")
        v = self.state().get("roster_version", 0)
        self.tw("thread", "set", "db", "--branch", "quix/zorblax")
        self.assertEqual(self.branch(), "quix/zorblax")
        self.assertGreater(self.state().get("roster_version", 0), v)

    def test_set_branch_refused_once_seated(self):
        self.tw("thread", "add", "db", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("branch is fixed", r.stderr)
        self.assertEqual(self.branch(), "tw/demo/db")
        self.assertNotEqual(self.tw("thread", "reseat", "db", "--session", "session_01Quixbar", "--branch", "x/y",
                                    ok=False).returncode, 0)

    def test_branch_names_checked_and_not_shared(self):
        self.tw("thread", "add", "db")
        for bad in ("-x", "a..b", "a b", "x/", "x.lock", ""):
            self.assertNotEqual(self.tw("thread", "set", "db", "--branch", bad, ok=False).returncode, 0, bad)
        self.assertNotEqual(self.tw("thread", "add", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "set", "ui", "--branch", "tw/demo/db", ok=False).returncode, 0)
        self.assertEqual(self.branch(), "tw/demo/db")

    def test_branch_clashes_and_bad_names(self):
        self.tw("thread", "add", "db")
        self.tw("thread", "add", "web")
        for bad in ("tw/demo/db", "TW/DEMO/DB", "tw/demo/db/x", "tw", "main", "origin/main", "HEAD", "a.lock/b",
                    "a;b", "x/.y"):
            r = self.tw("thread", "set", "web", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        # ui's default name, before ui exists: refused (T38 M1), so ui can still be added later
        self.assertNotEqual(self.tw("thread", "set", "web", "--branch", "tw/demo/ui", ok=False).returncode, 0)
        self.tw("thread", "add", "ui")
        self.assertNotEqual(self.tw("thread", "add", "q3", "--branch", "main", ok=False).returncode, 0)
        self.tw("thread", "add", "s1", "--session", "session_01Zorblax")
        self.tw("thread", "set", "s1", "--branch", "tw/demo/s1")  # its own branch again: a no-op, allowed

    def test_branch_fixed_once_worktree_exists(self):
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "db")
        self.addCleanup(shutil.rmtree, str(Path(self.state()["threads"]["db"]["worktree"]).parent), True)
        self.tw("worktree", "db")
        r = self.tw("thread", "set", "db", "--branch", "quix/zorblax", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("worktree", r.stderr)
        st = self.state()
        st["threads"]["db"]["branch"] = "quix/zorblax"  # a ledger edited by hand
        (self.dir / ".teamwork" / "state.json").write_text(json.dumps(st))
        r = self.tw("worktree", "db", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is on tw/demo/db", r.stderr)

    def test_t32_leftovers_namespace_base_remotes_missing_field(self):
        """T38: M1 tw/<project>/<other>, M3 tw/<project> itself, M2 inside the base and other remotes, M6 no field."""
        self.tw("thread", "add", "db")
        for bad in ("tw/demo/zorb", "tw/demo/web/x", "tw/demo", "TW/Demo", "main/x"):
            r = self.tw("thread", "set", "db", "--branch", bad, ok=False)
            self.assertNotEqual(r.returncode, 0, bad)
        self.tw("thread", "set", "db", "--branch", "tw/demo/db")  # its own default name is fine
        self.tw("thread", "set", "db", "--branch", "quix/db")
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "upstream", "https://example.invalid/x.git"],
                       check=True)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "upstream/main", ok=False).returncode, 0)
        self.assertNotEqual(self.tw("thread", "set", "db", "--branch", "Upstream/x", ok=False).returncode, 0)
        subprocess.run(["git", "-C", str(self.dir), "remote", "add", "tw", "https://example.invalid/y.git"], check=True)
        self.tw("thread", "add", "ui")  # a remote named tw does not block the default branches
        stf = self.dir / ".teamwork" / "state.json"
        st = json.loads(stf.read_text())
        del st["threads"]["db"]["branch"]  # a ledger edited by hand
        stf.write_text(json.dumps(st))
        self.assertIn("tw/demo/db", self.tw("worker-prompt", "db").stdout)  # falls back to the default name
        self.tw("thread", "set", "db", "--branch", "quix/db2")
        self.assertEqual(self.branch(), "quix/db2")

    def test_t32_leftovers_detached_head_and_orphaned_branch(self):
        """T38: M4 a detached worktree is refused; M5 a removed worktree's branch is not orphaned by a rename."""
        subprocess.run(["git", "-C", str(self.dir), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        self.tw("thread", "add", "web")
        wt = self.state()["threads"]["web"]["worktree"]
        self.addCleanup(shutil.rmtree, str(Path(wt).parent), True)
        self.tw("worktree", "web")
        subprocess.run(["git", "-C", wt, "checkout", "-q", "--detach"], check=True)
        r = self.tw("worktree", "web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("detached", r.stderr)
        subprocess.run(["git", "-C", str(self.dir), "worktree", "remove", wt], check=True)
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("orphan", r.stderr)
        self.assertEqual(self.branch("web"), "tw/demo/web")
        self.tw("thread", "set", "web", "--session", "session_01Zorblax")
        r = self.tw("thread", "set", "web", "--branch", "quix/web", ok=False)
        self.assertIn("planned", r.stderr)  # a seated thread hears the real reason

    def test_private_scratch_is_ignored_and_documented(self):
        self.tw("thread", "add", "ui")
        p = self.tw("worker-prompt", "ui").stdout
        self.assertIn("**Private scratch never goes to `/tmp`**", p)
        self.assertIn(f"{self.state()['repo']['path']}/.teamwork/private/ui/tmp/", p)
        self.assertIn("never a `.teamwork/` inside your worktree", p)
        subprocess.run(["git", "-C", str(self.dir), "init", "-q"], check=True)
        f = self.dir / ".teamwork" / "private" / "ui" / "tmp" / "zorblax.txt"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        r = subprocess.run(["git", "-C", str(self.dir), "check-ignore", "-q", str(f)])
        self.assertEqual(r.returncode, 0)  # ignored by .teamwork/.gitignore from init


if __name__ == "__main__":
    unittest.main()
