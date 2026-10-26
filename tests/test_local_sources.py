import subprocess
from pathlib import Path

import pytest

from tools import local_stack


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            *args,
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.setattr(local_stack, "PROJECT_ROOT", tmp_path)
    for name, branch in local_stack.PROJECT_BRANCHES.items():
        remote = tmp_path / "remotes" / f"{name}.git"
        remote.mkdir(parents=True)
        git(remote, "init", "--bare")
        directory = tmp_path / name
        directory.mkdir()
        git(directory, "init", "-b", branch)
        (directory / "README").write_text("source\n")
        git(directory, "add", "README")
        git(directory, "commit", "-m", "initial")
        git(directory, "remote", "add", "origin", str(remote))
        git(directory, "push", "-u", "origin", branch)
    return tmp_path


def test_defaults_use_primary_directories_without_a_config(projects):
    config, paths = local_stack.primary_paths(
        projects / "AgentTesting/local-stack.toml"
    )
    assert config == {}
    assert paths["ai_dir"] == projects / "EpochAI"
    assert paths["ui_dir"] == projects / "EpochClientPortal"
    assert (
        paths["backend_binary"]
        == projects / "EpochBackend/build/bin/epoch_stratifyx_server"
    )
    records = local_stack.verify_primary_checkouts()
    assert {
        name: item["branch"] for name, item in records.items()
    } == local_stack.PROJECT_BRANCHES
    for name, item in records.items():
        assert item["commit"] == git(projects / name, "rev-parse", "HEAD")


def test_old_worktree_config_is_rejected_before_git_or_auth(projects):
    config = projects / "local-stack.toml"
    config.write_text('ai_dir="EpochAI-old-worktree"\n')
    with pytest.raises(ValueError, match="Worktree overrides are not enabled"):
        local_stack.primary_paths(config)


def test_binary_cannot_be_symlinked_from_a_worktree(projects):
    outside = projects / "old-server"
    outside.touch()
    binary = projects / "EpochBackend/build/bin/epoch_stratifyx_server"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(outside)
    with pytest.raises(ValueError, match="must not be redirected"):
        local_stack.primary_paths(projects / "missing.toml")


def test_binary_cannot_come_from_a_nested_worktree(projects):
    nested = projects / "EpochBackend/.worktrees/feature/build/bin/server"
    nested.parent.mkdir(parents=True)
    nested.touch()
    binary = projects / "EpochBackend/build/bin/epoch_stratifyx_server"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(nested)
    with pytest.raises(ValueError, match="must not be redirected"):
        local_stack.primary_paths(projects / "missing.toml")


def test_wrong_primary_branch_is_rejected(projects):
    git(projects / "EpochAI", "switch", "-c", "feature")
    with pytest.raises(ValueError, match="check out dev"):
        local_stack.verify_primary_checkouts()


def test_remote_changes_are_checked_without_a_cached_fetch(projects):
    source = projects / "EpochAI"
    clone = projects / "other-agent"
    git(
        projects,
        "clone",
        "--branch",
        "dev",
        str(projects / "remotes/EpochAI.git"),
        str(clone),
    )
    git(clone, "commit", "--allow-empty", "-m", "new remote work")
    git(clone, "push", "origin", "dev")
    assert git(source, "rev-parse", "HEAD") == git(source, "rev-parse", "origin/dev")
    with pytest.raises(ValueError, match="not synchronized"):
        local_stack.verify_primary_checkouts()


def test_clean_local_commits_can_be_tested_before_pushing(projects):
    source = projects / "EpochAI"
    remote = git(source, "rev-parse", "origin/dev")
    git(source, "commit", "--allow-empty", "-m", "not pushed")
    record = local_stack.verify_primary_checkouts()["EpochAI"]
    assert record["commit"] == git(source, "rev-parse", "HEAD")
    assert record["origin_commit"] == remote
    assert record["commit"] != remote


@pytest.mark.parametrize("diverged", [False, True])
def test_behind_or_diverged_local_commits_are_rejected(projects, diverged):
    source = projects / "EpochAI"
    remote = git(source, "rev-parse", "HEAD")
    git(source, "commit", "--allow-empty", "-m", "remote work")
    git(source, "push", "origin", "dev")
    git(source, "reset", "--hard", remote)
    if diverged:
        git(source, "commit", "--allow-empty", "-m", "different local work")
    with pytest.raises(ValueError, match="not synchronized"):
        local_stack.verify_primary_checkouts()


def test_uncommitted_source_edits_are_rejected(projects):
    (projects / "EpochBackend/README").write_text("changed\n")
    with pytest.raises(ValueError, match="uncommitted tracked changes"):
        local_stack.verify_primary_checkouts()


def test_linked_worktree_at_primary_path_is_rejected(projects):
    primary = projects / "EpochAI"
    moved = projects / "original-ai"
    primary.rename(moved)
    git(moved, "worktree", "add", "--detach", str(primary), "HEAD")
    with pytest.raises(ValueError, match="not a linked worktree"):
        local_stack.verify_primary_checkouts()


def test_untracked_local_artifacts_are_preserved(projects):
    extra = projects / "EpochBackend/local-notes.txt"
    extra.write_text("keep me")
    assert local_stack.verify_primary_checkouts()["EpochBackend"]["branch"] == "main"
    assert extra.read_text() == "keep me"
