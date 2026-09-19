"""Tests for `cherry_pick_prs` helpers and repository operations."""

import contextlib
import functools
import io
import pathlib
import subprocess
import tempfile
from unittest import mock

from absl.testing import absltest
from absl.testing import parameterized
from tools import cherry_pick_prs


class ParsePrNumberTest(parameterized.TestCase):
    @parameterized.named_parameters(
        ("plain_number", "61", 61),
        ("hash_number", "#61", 61),
        ("pull_url", "https://github.com/google-deepmind/xmanager/pull/58", 58),
        ("pull_url_with_suffix", "github.com/o/r/pull/12/files", 12),
    )
    def test_detects_pr(self, spec, expected):
        self.assertEqual(cherry_pick_prs.parse_pr_number(spec), expected)

    @parameterized.named_parameters(
        ("branch", "upgrade-sqlalchemy"),
        ("branch_with_title", "my-branch:Some title"),
        ("branch_with_digits", "release-12"),
        ("empty", ""),
    )
    def test_rejects_non_pr(self, spec):
        self.assertIsNone(cherry_pick_prs.parse_pr_number(spec))


class SplitBranchSpecTest(parameterized.TestCase):
    @parameterized.named_parameters(
        ("no_title", "my-branch", ("my-branch", None)),
        ("with_title", "my-branch:Custom title", ("my-branch", "Custom title")),
        (
            "title_with_colon",
            "my-branch:Fix: the thing",
            ("my-branch", "Fix: the thing"),
        ),
        ("trailing_colon", "my-branch:", ("my-branch", None)),
    )
    def test_split(self, spec, expected):
        self.assertEqual(cherry_pick_prs.split_branch_spec(spec), expected)


class FormatCommitMessageTest(parameterized.TestCase):
    def test_pr_appends_number(self):
        self.assertEqual(
            cherry_pick_prs.format_commit_message("Upgrade `sqlalchemy`", 61),
            "Upgrade `sqlalchemy` (#61)",
        )

    def test_branch_keeps_title(self):
        self.assertEqual(
            cherry_pick_prs.format_commit_message("Add `.gitignore`", None),
            "Add `.gitignore`",
        )


class OverlapWarningsTest(absltest.TestCase):
    def _commit(self, spec, files):
        source = cherry_pick_prs.Source(
            spec=spec, kind="branch", head="deadbeef", message=spec
        )
        return cherry_pick_prs.StackedCommit(source=source, sha="sha", files=files)

    def test_reports_shared_files_only(self):
        commits = [
            self._commit("#1", ["a.py", "b.py"]),
            self._commit("#2", ["b.py", "c.py"]),
            self._commit("#3", ["d.py"]),
        ]
        warnings = cherry_pick_prs.overlap_warnings(commits)
        self.assertEqual(warnings, ["b.py touched by: #1, #2"])

    def test_no_overlap(self):
        commits = [
            self._commit("#1", ["a.py"]),
            self._commit("#2", ["b.py"]),
        ]
        self.assertEqual(cherry_pick_prs.overlap_warnings(commits), [])


class CherryPickPrsIntegrationTest(parameterized.TestCase):
    def setUp(self):
        super().setUp()
        self.repo = pathlib.Path(self.enter_context(tempfile.TemporaryDirectory()))
        self._git("init", "--initial-branch=main")
        self._git("config", "user.name", "Test Author")
        self._git("config", "user.email", "author@example.com")
        self._git("config", "commit.gpgsign", "false")
        self._git("config", "core.hooksPath", "/dev/null")
        self.base_sha = self._commit("shared.txt", "base\n", "Base")
        run_git = functools.partial(cherry_pick_prs.run_git, cwd=str(self.repo))
        self.enter_context(mock.patch.object(cherry_pick_prs, "run_git", run_git))

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _commit(self, path, content, subject):
        (self.repo / path).write_text(content)
        self._git("add", path)
        self._git("commit", "-m", subject)
        return self._git("rev-parse", "HEAD")

    def _source(self, name="source", path="source.txt", content="source\n"):
        self._git("checkout", "-b", name, "main")
        sha = self._commit(path, content, f"Apply {name}")
        self._git("checkout", "main")
        return sha

    def _run(self, **kwargs):
        kwargs.setdefault("to_branch", "target")
        kwargs.setdefault("sources", ["source"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cherry_pick_prs.cherry_pick_prs(cherry_pick_prs.Args(**kwargs))
        return output.getvalue()

    def _assert_restored(self, branch="main", sha=None):
        if branch is None:
            self.assertEqual(self._git("rev-parse", "--abbrev-ref", "HEAD"), "HEAD")
        else:
            self.assertEqual(self._git("symbolic-ref", "--short", "HEAD"), branch)
        if sha is not None:
            self.assertEqual(self._git("rev-parse", "HEAD"), sha)
        self.assertEmpty(self._git("status", "--porcelain"))
        self.assertEmpty(self._git("branch", "--list", "cherry-pick-prs-tmp-*"))

    def test_appends_to_existing_target(self):
        self._source()
        self._git("checkout", "-b", "target", "main")
        target_sha = self._commit("target.txt", "target\n", "Keep target")
        self._git("checkout", "main")

        self._run()

        self.assertEqual(self._git("rev-parse", "target^"), target_sha)
        self.assertEqual(self._git("show", "target:target.txt"), "target")
        self.assertEqual(self._git("show", "target:source.txt"), "source")
        self._assert_restored(sha=self.base_sha)

    @parameterized.named_parameters(("overwrite", "overwrite"), ("force", "force"))
    def test_overwrite_rebuilds_updated_source_with_same_title(self, flag):
        self._source(content="original\n")
        self._git("branch", "target", "main")
        self._run()
        self._git("checkout", "target")
        stale_sha = self._commit("stale.txt", "stale\n", "Drop stale commit")
        self._git("checkout", "source")
        self._commit("source.txt", "updated\n", "Apply source")
        self._git("checkout", "main")

        self._run(base_branch="main", **{flag: True})

        self.assertEqual(self._git("rev-parse", "target^"), self.base_sha)
        self.assertEqual(self._git("show", "target:source.txt"), "updated")
        self.assertNotIn("stale.txt", self._git("ls-tree", "--name-only", "target"))
        self.assertNotEqual(self._git("rev-parse", "target"), stale_sha)
        self._assert_restored(sha=self.base_sha)

    @parameterized.named_parameters(("overwrite", "overwrite"), ("force", "force"))
    def test_overwrite_requires_explicit_base(self, flag):
        self._source()
        self._git("branch", "target", "main")

        with self.assertRaisesRegex(cherry_pick_prs.CherryPickError, "base"):
            self._run(**{flag: True})

        self.assertEqual(self._git("rev-parse", "target"), self.base_sha)
        self._assert_restored(sha=self.base_sha)

    def test_explicit_base_requires_overwrite_for_existing_target(self):
        self._source()
        self._git("branch", "target", "main")

        with self.assertRaisesRegex(cherry_pick_prs.CherryPickError, "overwrite"):
            self._run(base_branch="main")

        self.assertEqual(self._git("rev-parse", "target"), self.base_sha)
        self._assert_restored(sha=self.base_sha)

    def test_creates_missing_target_from_explicit_base(self):
        self._source()

        self._run(base_branch="main")

        self.assertEqual(self._git("rev-parse", "target^"), self.base_sha)
        self.assertEqual(self._git("show", "target:source.txt"), "source")
        self._assert_restored(sha=self.base_sha)

    @parameterized.named_parameters(("existing", True), ("missing", False))
    def test_dry_run_leaves_target_unchanged(self, target_exists):
        self._source()
        if target_exists:
            self._git("branch", "target", "main")

        with mock.patch.object(
            cherry_pick_prs, "stack_source", wraps=cherry_pick_prs.stack_source
        ) as stack_source:
            output = self._run(
                base_branch="main", overwrite=target_exists, dry_run=True
            )

        stack_source.assert_called_once()
        self.assertIn("DRY RUN", output)
        if target_exists:
            self.assertEqual(self._git("rev-parse", "target"), self.base_sha)
        else:
            self.assertEmpty(self._git("branch", "--list", "target"))
        self._assert_restored(sha=self.base_sha)

    def test_conflict_preserves_target_and_starting_branch(self):
        self._source("first", "shared.txt", "first\n")
        self._source("second", "shared.txt", "second\n")
        self._git("branch", "target", "main")

        with self.assertRaisesRegex(cherry_pick_prs.CherryPickError, "conflict"):
            self._run(sources=["first", "second"])

        self.assertEqual(self._git("rev-parse", "target"), self.base_sha)
        self._assert_restored(sha=self.base_sha)

    @parameterized.named_parameters(("apply", False), ("dry_run", True))
    def test_restores_detached_head_separate_from_target(self, dry_run):
        self._source()
        self._git("checkout", "-b", "target", "main")
        self._commit("target.txt", "target\n", "Keep target")
        self._git("checkout", "--detach", self.base_sha)

        self._run(dry_run=dry_run)

        self._assert_restored(branch=None, sha=self.base_sha)

    def test_restores_checked_out_target_after_update(self):
        self._source()
        self._git("checkout", "-b", "target", "main")

        self._run()

        self.assertEqual((self.repo / "source.txt").read_text(), "source\n")
        self._assert_restored(branch="target")

    @parameterized.named_parameters(
        ("branch", "target", False), ("detached_none", "None", True)
    )
    def test_rejects_target_checked_out_in_other_worktree(self, target, detached):
        self._source()
        self._git("branch", "target", "main")
        if target != "target":
            self._git("branch", "-m", "target", target)
        other_worktree = self.enter_context(tempfile.TemporaryDirectory())
        self._git("worktree", "add", other_worktree, target)
        if detached:
            self._git("checkout", "--detach", self.base_sha)

        with mock.patch.object(cherry_pick_prs, "resolve_source") as resolve_source:
            with self.assertRaisesRegex(cherry_pick_prs.CherryPickError, "worktree"):
                self._run(to_branch=target)

        resolve_source.assert_not_called()
        self.assertEqual(self._git("rev-parse", target), self.base_sha)
        self._assert_restored(branch=None if detached else "main", sha=self.base_sha)

    def test_rejects_tag_as_existing_target(self):
        self._source()
        self._git("tag", "target", "main")

        with self.assertRaises(cherry_pick_prs.CherryPickError):
            self._run()

        self.assertEmpty(self._git("branch", "--list", "target"))
        self.assertEqual(self._git("rev-parse", "refs/tags/target"), self.base_sha)
        self._assert_restored(sha=self.base_sha)

    def test_creates_target_sharing_tag_name(self):
        self._source()
        self._git("tag", "target", "main")

        self._run(base_branch="main")

        self.assertEqual(self._git("rev-parse", "refs/heads/target^"), self.base_sha)
        self.assertEqual(self._git("show", "refs/heads/target:source.txt"), "source")
        self.assertEqual(self._git("rev-parse", "refs/tags/target"), self.base_sha)
        self._assert_restored(sha=self.base_sha)

    def test_rejects_head_as_target(self):
        self._source()

        with self.assertRaises(cherry_pick_prs.CherryPickError):
            self._run(to_branch="HEAD", base_branch="main", overwrite=True)

        self._assert_restored(sha=self.base_sha)

    def test_creates_target_when_base_contains_source(self):
        source_sha = self._source()

        self._run(base_branch="source")

        self.assertEqual(self._git("rev-parse", "target"), source_sha)
        self._assert_restored(sha=self.base_sha)

    def test_rejects_concurrent_target_change(self):
        self._source()
        concurrent_sha = self._source("concurrent", "concurrent.txt", "concurrent\n")
        self._git("branch", "target", "main")
        stack_source = cherry_pick_prs.stack_source

        def stack_and_move_target(source, base_point):
            result = stack_source(source, base_point)
            self._git("update-ref", "refs/heads/target", concurrent_sha)
            return result

        with mock.patch.object(
            cherry_pick_prs, "stack_source", side_effect=stack_and_move_target
        ):
            with self.assertRaises(cherry_pick_prs.CherryPickError):
                self._run()

        self.assertEqual(self._git("rev-parse", "target"), concurrent_sha)
        self._assert_restored(sha=self.base_sha)


if __name__ == "__main__":
    absltest.main()
