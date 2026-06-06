"""Unit tests for the pure helpers in ``cherry_pick_prs``."""

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


if __name__ == "__main__":
    absltest.main()
