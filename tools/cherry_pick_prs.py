"""Stack GitHub PRs / local branches as squashed commits onto a branch.

Given an ordered list of GitHub PRs and/or local branches and a target
"to-cherry-pick-to" branch, this tool squashes each source into a single commit
and stacks them on top of the target branch, in order. The squash commit is
named the way GitHub names a squash+rebase merge, i.e. ``Title (#123)`` for PRs
and ``Title`` for local branches.

Disjointness is decided by git: each source is applied with ``git cherry-pick``
on top of the accumulating stack, and a cherry-pick conflict is treated as the
sources not being disjoint, which aborts the whole operation. Everything happens
on a throwaway temp branch and the target branch is only moved on full success,
so a failure leaves the repository untouched.

Example:

    python tools/cherry_pick_prs.py \\
        --to_branch cherry-pick-staging \\
        --sources '#51' '#53' '#54' '#55' '#56' '#58' '#61'
"""

import dataclasses
import json
import re
import subprocess
import time
from typing import Sequence

from absl import app
from absl import logging
from etils import eapp
import simple_parsing


@dataclasses.dataclass
class Args:
    """Command-line arguments.

    Attributes:
        to_branch: Local "to-cherry-pick-to" branch that sources are stacked on.
        sources: Ordered sources to stack. Each is a PR (a number, optionally
            hash-prefixed, or a PR URL), a local branch (``my-branch``), or a
            local branch with an explicit commit-title override
            (``my-branch:Custom title``).
        repo: ``owner/name`` GitHub repository used for PR lookups.
        remote: Git remote used to fetch PR heads.
        dry_run: Build and verify the stack on a temp branch but do not move
            ``to_branch``.
        force: Allow moving ``to_branch`` even when the move would not be a
            fast-forward (i.e. would drop commits already on ``to_branch``).
    """

    to_branch: str
    sources: list[str] = dataclasses.field(default_factory=list)
    repo: str = "google-deepmind/xmanager"
    remote: str = "origin"
    dry_run: bool = False
    force: bool = False


class CherryPickError(RuntimeError):
    """Raised when stacking cannot proceed (e.g. a non-disjoint conflict)."""


@dataclasses.dataclass
class Source:
    """A resolved source to be squashed into a single commit.

    Attributes:
        spec: The original token from ``--sources``.
        kind: Either ``"pr"`` or ``"branch"``.
        head: Git ref (sha) of the source tip to squash up to.
        message: Commit message (subject) for the squashed commit.
        pr_number: PR number when ``kind == "pr"``, else ``None``.
    """

    spec: str
    kind: str
    head: str
    message: str
    pr_number: int | None = None


@dataclasses.dataclass
class StackedCommit:
    """Result of stacking a single source.

    Attributes:
        source: The source that was squashed.
        sha: The resulting squash commit sha.
        files: Files touched by the squash commit.
    """

    source: Source
    sha: str
    files: list[str]


def run_git(
    *args: str, check: bool = True, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Runs ``git`` with the given arguments and captures its output."""
    return _run("git", *args, check=check, cwd=cwd)


def run_gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Runs ``gh`` with the given arguments and captures its output."""
    return _run("gh", *args, check=check)


def _run(
    *args: str, check: bool, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    logging.debug("running: %s", " ".join(args))
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise CherryPickError(
            f"command {' '.join(args)!r} failed with code "
            f"{result.returncode}:\n{result.stderr.strip()}"
        )
    return result


# A token is a PR if it is all digits, "#123", or a pull-request URL.
_PR_NUMBER_RE = re.compile(r"^#?(?P<number>\d+)$")
_PR_URL_RE = re.compile(r"/pull/(?P<number>\d+)")


def parse_pr_number(spec: str) -> int | None:
    """Returns the PR number for ``spec`` if it denotes a PR, else ``None``."""
    match = _PR_NUMBER_RE.match(spec)
    if match is not None:
        return int(match.group("number"))
    match = _PR_URL_RE.search(spec)
    if match is not None:
        return int(match.group("number"))
    return None


def split_branch_spec(spec: str) -> tuple[str, str | None]:
    """Splits a ``branch[:title]`` token into ``(branch, title_or_none)``."""
    branch, separator, title = spec.partition(":")
    if separator and title:
        return branch, title
    return branch, None


def format_commit_message(title: str, pr_number: int | None) -> str:
    """Formats a GitHub-style squash subject (``Title (#123)`` or ``Title``)."""
    if pr_number is None:
        return title
    return f"{title} (#{pr_number})"


def resolve_pr(spec: str, pr_number: int, repo: str, remote: str) -> Source:
    """Resolves a PR source: fetch its head and read its title via ``gh``."""
    logging.info("resolving PR #%d via gh (repo %s)", pr_number, repo)
    view = run_gh(
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        "number,title,headRefName,headRefOid,baseRefName,state",
    )
    metadata = json.loads(view.stdout)
    title = metadata["title"]
    state = metadata.get("state")
    if state != "OPEN":
        logging.warning("PR #%d is not OPEN (state=%s)", pr_number, state)

    # Fetch the PR head into FETCH_HEAD; this works for forked PRs too.
    run_git("fetch", remote, f"pull/{pr_number}/head")
    head = run_git("rev-parse", "FETCH_HEAD").stdout.strip()

    return Source(
        spec=spec,
        kind="pr",
        head=head,
        message=format_commit_message(title, pr_number),
        pr_number=pr_number,
    )


def resolve_branch(spec: str) -> Source:
    """Resolves a local-branch source, honoring an optional title override."""
    branch, title_override = split_branch_spec(spec)
    rev = run_git("rev-parse", "--verify", "--quiet", branch, check=False)
    if rev.returncode != 0:
        raise CherryPickError(f"local branch {branch!r} not found")
    head = rev.stdout.strip()

    if title_override is not None:
        title = title_override
    else:
        # Default the title to the subject of the branch tip commit.
        title = run_git("log", "-1", "--format=%s", head).stdout.strip()

    return Source(
        spec=spec,
        kind="branch",
        head=head,
        message=format_commit_message(title, None),
        pr_number=None,
    )


def resolve_source(spec: str, repo: str, remote: str) -> Source:
    """Resolves a single ``--sources`` token into a :class:`Source`."""
    pr_number = parse_pr_number(spec)
    if pr_number is not None:
        return resolve_pr(spec, pr_number, repo, remote)
    return resolve_branch(spec)


def is_ancestor(maybe_ancestor: str, descendant: str) -> bool:
    """Returns whether ``maybe_ancestor`` is an ancestor of ``descendant``."""
    return (
        run_git(
            "merge-base", "--is-ancestor", maybe_ancestor, descendant, check=False
        ).returncode
        == 0
    )


def effective_base(head: str, prior_heads: Sequence[str]) -> str:
    """Returns the commit a source's own changes should be cherry-picked from.

    This makes stacked PRs work: a source built on top of an already-stacked
    source contributes only its own incremental commits rather than re-applying
    its dependency's commits (which would conflict when the dependency further
    modified the same files). The base is the descendant-most commit among the
    source's fork point (its merge base with the current stack) and any
    previously-stacked heads that are ancestors of ``head``.
    """
    fork_point = run_git("merge-base", head, "HEAD").stdout.strip()
    candidates = [fork_point]
    candidates.extend(h for h in prior_heads if is_ancestor(h, head))

    best = candidates[0]
    for candidate in candidates[1:]:
        if is_ancestor(best, candidate):
            best = candidate
    return best


def conflicting_files() -> list[str]:
    """Returns the unmerged (conflicting) paths in the current index."""
    result = run_git("diff", "--name-only", "--diff-filter=U", check=False)
    return [line for line in result.stdout.splitlines() if line]


def commit_files(sha: str) -> list[str]:
    """Returns the files changed by commit ``sha``."""
    result = run_git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    return [line for line in result.stdout.splitlines() if line]


def staged_is_empty() -> bool:
    """Returns whether the index has no staged changes relative to HEAD."""
    return run_git("diff", "--cached", "--quiet", check=False).returncode == 0


def already_stacked(message: str, head: str) -> bool:
    """Returns whether a squash commit for this source is already on HEAD.

    The tool names every squash commit deterministically (``Title (#N)``), so a
    matching subject among the commits added since the source's fork point means
    the source has already been stacked. This is more robust than re-merging the
    content, which can spuriously conflict when a *later* source modified the
    same files on top of this one.
    """
    merge_base = run_git("merge-base", "HEAD", head).stdout.strip()
    subjects = run_git("log", "--format=%s", f"{merge_base}..HEAD").stdout.splitlines()
    return message in subjects


def stack_source(source: Source, base_point: str) -> StackedCommit | None:
    """Squashes ``source`` into a single commit on top of the current HEAD.

    Idempotency comes first from the squash commit subject (``Title (#N)``): if
    one is already present on the target the source is skipped, which is robust
    even when a later source modified the same files on top of this one.
    Otherwise the source's own commits (``base_point..head``) are cherry-picked
    without committing and recorded as one squash commit. ``base_point`` is the
    source's :func:`effective_base`, so a source stacked on another contributes
    only its incremental changes.

    A cherry-pick conflict means the source is not disjoint from what is already
    on the stack and is surfaced as a :class:`CherryPickError`. Returns ``None``
    (and logs a warning) when there is nothing new to commit.
    """
    if already_stacked(source.message, source.head):
        logging.warning(
            "skipping %s: already stacked as %r", source.spec, source.message
        )
        return None

    logging.info(
        "stacking %s (%s..%s) as %r",
        source.spec,
        base_point[:9],
        source.head[:9],
        source.message,
    )

    cherry_pick = run_git(
        "cherry-pick", "--no-commit", f"{base_point}..{source.head}", check=False
    )
    if cherry_pick.returncode != 0:
        files = conflicting_files()
        run_git("cherry-pick", "--abort", check=False)
        run_git("reset", "--hard", "HEAD", check=False)
        conflict_list = "\n  ".join(files) if files else "(see git output)"
        raise CherryPickError(
            f"source {source.spec!r} is not disjoint; cherry-pick conflicted "
            f"on:\n  {conflict_list}\n{cherry_pick.stderr.strip()}"
        )

    if staged_is_empty():
        # Changes already present on the target; skip so re-runs are idempotent.
        run_git("reset", "--hard", "HEAD", check=False)
        run_git("cherry-pick", "--quit", check=False)
        logging.warning(
            "skipping %s: changes already present on the target", source.spec
        )
        return None

    run_git("commit", "--no-verify", "-m", source.message)
    sha = run_git("rev-parse", "HEAD").stdout.strip()
    return StackedCommit(source=source, sha=sha, files=commit_files(sha))


def ensure_clean_worktree() -> None:
    """Raises if the working tree has tracked staged or unstaged changes.

    Untracked files are ignored: they are never staged by ``cherry-pick
    --no-commit`` nor folded into the squash commits, so they cannot pollute the
    result. Tracked changes do block, since they would ride along onto the temp
    branch and be captured by the per-source ``git commit``.
    """
    status = run_git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if status:
        raise CherryPickError(
            f"working tree has tracked changes; commit or stash them first:\n{status}"
        )


def unique_temp_branch() -> str:
    """Returns a branch name that does not collide with an existing ref.

    A flat (slash-free) name is used so it cannot conflict with an existing
    branch such as ``cherry-pick-prs`` (git refuses to nest ``a/b`` under an
    existing ref file ``a``).
    """
    base = f"cherry-pick-prs-tmp-{int(time.time())}"
    candidate = base
    suffix = 0
    while (
        run_git("rev-parse", "--verify", "--quiet", candidate, check=False).returncode
        == 0
    ):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def current_branch() -> str | None:
    """Returns the checked-out branch name, or ``None`` when detached."""
    result = run_git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    name = result.stdout.strip()
    return name or None


def overlap_warnings(commits: Sequence[StackedCommit]) -> list[str]:
    """Returns warnings for files touched by more than one source."""
    seen: dict[str, list[str]] = {}
    for commit in commits:
        for path in commit.files:
            seen.setdefault(path, []).append(commit.source.spec)
    warnings = []
    for path, specs in sorted(seen.items()):
        if len(specs) > 1:
            warnings.append(f"{path} touched by: {', '.join(specs)}")
    return warnings


def report(
    commits: Sequence[StackedCommit],
    skipped: Sequence[str],
    to_branch: str,
    final_sha: str,
    original_sha: str,
    dry_run: bool,
    moved: bool,
) -> None:
    """Prints a human-readable summary of the stacking result."""
    print("\nCherry-pick / stack result")
    print("=" * 72)
    if commits:
        for commit in commits:
            print(f"  {commit.sha[:9]}  {commit.source.message}")
    else:
        print("  (no new commits)")
    print("-" * 72)

    if skipped:
        print("Skipped (already present / no unique changes):")
        for spec in skipped:
            print(f"  - {spec}")
        print("-" * 72)

    warnings = overlap_warnings(commits)
    if warnings:
        print("Files touched by multiple sources (applied cleanly):")
        for warning in warnings:
            print(f"  - {warning}")
        print("-" * 72)

    counts = f"{len(commits)} stacked, {len(skipped)} skipped"
    if final_sha == original_sha:
        print(f"{to_branch}: already up to date at {original_sha[:9]} ({counts}).")
    elif dry_run:
        print(
            f"DRY RUN: {to_branch} left at {original_sha[:9]}; "
            f"verified stack head is {final_sha[:9]} ({counts})."
        )
    elif moved:
        print(f"{to_branch}: {original_sha[:9]} -> {final_sha[:9]} ({counts}).")
    print("=" * 72)


def cherry_pick_prs(args: Args) -> None:
    """Resolves, verifies, and stacks all sources onto ``args.to_branch``."""
    if not args.sources:
        raise CherryPickError("no sources provided; pass --sources ...")

    ensure_clean_worktree()

    to_branch_sha = run_git(
        "rev-parse", "--verify", "--quiet", args.to_branch, check=False
    )
    if to_branch_sha.returncode != 0:
        raise CherryPickError(f"target branch {args.to_branch!r} not found")
    original_sha = to_branch_sha.stdout.strip()

    starting_branch = current_branch()
    sources = [resolve_source(spec, args.repo, args.remote) for spec in args.sources]

    temp_branch = unique_temp_branch()
    run_git("checkout", "-b", temp_branch, args.to_branch)

    try:
        commits = []
        skipped: list[str] = []
        prior_heads: list[str] = []
        for source in sources:
            if any(is_ancestor(source.head, prior) for prior in prior_heads):
                logging.warning(
                    "skipping %s: already fully contained in an earlier source",
                    source.spec,
                )
                skipped.append(source.spec)
                prior_heads.append(source.head)
                continue
            base_point = effective_base(source.head, prior_heads)
            stacked = stack_source(source, base_point)
            if stacked is not None:
                commits.append(stacked)
            else:
                skipped.append(source.spec)
            prior_heads.append(source.head)
        final_sha = run_git("rev-parse", "HEAD").stdout.strip()

        moved = False
        if final_sha == original_sha:
            logging.info("%s is already up to date", args.to_branch)
        elif not args.dry_run:
            # The stack is always built on top of to_branch, so moving it is
            # normally a fast-forward. Guard the rare non-fast-forward case (it
            # would drop commits) behind --force.
            if not is_ancestor(original_sha, final_sha) and not args.force:
                raise CherryPickError(
                    f"refusing to move {args.to_branch!r}: result "
                    f"{final_sha[:9]} is not a fast-forward of its current "
                    f"commit {original_sha[:9]}; pass --force to override"
                )
            run_git("branch", "-f", args.to_branch, final_sha)
            moved = True
            logging.info(
                "moved %s from %s to %s",
                args.to_branch,
                original_sha[:9],
                final_sha[:9],
            )
    finally:
        # Return to wherever we started and drop the temp branch.
        run_git("checkout", starting_branch or original_sha, check=False)
        run_git("branch", "-D", temp_branch, check=False)

    report(
        commits, skipped, args.to_branch, final_sha, original_sha, args.dry_run, moved
    )


def main(args: Args) -> None:
    try:
        cherry_pick_prs(args)
    except CherryPickError as error:
        logging.error("%s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    eapp.better_logging()
    app.run(
        main,
        flags_parser=eapp.make_flags_parser(
            Args,
            conflict_resolution=simple_parsing.ConflictResolution.ALWAYS_MERGE,
            argument_generation_mode=simple_parsing.ArgumentGenerationMode.NESTED,
            nested_mode=simple_parsing.NestedMode.WITHOUT_ROOT,
        ),
    )
