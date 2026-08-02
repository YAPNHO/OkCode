from __future__ import annotations

from pathlib import Path

import pytest

from okcode.agents.manager import AgentCancelToken
from okcode.agents.models import (
    AgentIsolationMode,
    AgentLaunchKind,
    AgentLaunchRequest,
    AgentPermissionPolicy,
    AgentRole,
    AgentRoleSourceKind,
    AgentTaskStatus,
)
from okcode.agents.runner import AgentRunner
from okcode.models import ChatMessage, Role, StreamCompleted, TokenUsage, ToolCall
from okcode.permissions.models import PermissionMode
from okcode.tools.defaults import build_default_registry
from okcode.tools.workspace import Workspace
from okcode.worktrees import WorktreeManager
from okcode.worktrees.models import WorktreeIdentity, WorktreePrepareRequest
from tests.fakes import FakeProvider


def _git(repo: Path, *args: str) -> None:
    _git_output(repo, *args)


def _git_output(repo: Path, *args: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "init")
    return repo


def _role(repo: Path) -> AgentRole:
    return AgentRole(
        name="writer",
        description="写文件",
        source_kind=AgentRoleSourceKind.PROJECT,
        source_path=repo / "writer.md",
        tool_allowlist=("write_file",),
        permission_policy=AgentPermissionPolicy(resolved_mode=PermissionMode.ALLOW),
        system_prompt="你在隔离 worktree 中写文件。",
        isolation=AgentIsolationMode.WORKTREE,
    )


@pytest.mark.asyncio
async def test_defined_modifies_isolated_worktree_without_touching_main_repo(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    registry = build_default_registry(Workspace(repo))
    manager = WorktreeManager(repo)
    provider = FakeProvider(
        [
            [
                StreamCompleted(
                    ChatMessage(
                        Role.ASSISTANT,
                        tool_call=ToolCall(
                            "write-1",
                            "write_file",
                            '{"path":"tracked.txt","content":"worktree\\n"}',
                        ),
                    ),
                    TokenUsage(),
                ),
            ],
            [
                StreamCompleted(
                    ChatMessage(Role.ASSISTANT, content="已写入隔离 worktree"),
                    TokenUsage(1, 1, 2, True),
                ),
            ],
        ]
    )
    task_id = "1234567890abcdef"
    identity = WorktreeIdentity(
        name="agents/writer/task",
        branch="okcode/agents/agents/writer/task",
        task_id=task_id,
        parent_session_id="parent",
        role_name="writer",
        trigger="tool",
    )
    request = AgentLaunchRequest(
        task_id=task_id,
        kind=AgentLaunchKind.DEFINED,
        task="写入 tracked.txt",
        parent_session_id="parent",
        role=_role(repo),
        visible_tool_names=("write_file",),
        max_turns=3,
        permission_mode=PermissionMode.ALLOW,
        isolation=AgentIsolationMode.WORKTREE,
        worktree_request=WorktreePrepareRequest(identity, repo),
        main_workspace_root=repo,
    )
    runner = AgentRunner(
        lambda _: provider,
        registry,
        workspace_root=repo,
        worktree_manager=manager,
    )

    result = await runner.run(request, AgentCancelToken())

    assert result.status is AgentTaskStatus.COMPLETED
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "main\n"
    assert result.worktree is not None
    assert result.worktree.cleanup_decision.value == "kept"
    assert "tracked.txt" in result.worktree.changed_files
    assert (result.worktree.path / "tracked.txt").read_text(encoding="utf-8") == "worktree\n"


@pytest.mark.asyncio
async def test_fork_worktree_can_write_and_commit_without_touching_main_repo(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "witness.txt").write_text("original content from main agent\n", encoding="utf-8")
    _git(repo, "add", "witness.txt")
    _git(repo, "commit", "-m", "add witness")
    registry = build_default_registry(Workspace(repo))
    manager = WorktreeManager(repo)
    provider = FakeProvider(
        [
            [
                StreamCompleted(
                    ChatMessage(
                        Role.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                "write-1",
                                "write_file",
                                (
                                    '{"path":"witness.txt",'
                                    '"content":"modified by isolated worker\\n"}'
                                ),
                            ),
                            ToolCall(
                                "commit-1",
                                "run_command",
                                (
                                    '{"command":"git add witness.txt && '
                                    'git -c user.email=test@example.com '
                                    '-c user.name=Test commit -m '
                                    '\\\"isolated witness\\\""}'
                                ),
                            ),
                        ),
                    ),
                    TokenUsage(),
                ),
            ],
            [
                StreamCompleted(
                    ChatMessage(Role.ASSISTANT, content="已在隔离 worktree 中提交 witness.txt"),
                    TokenUsage(1, 1, 2, True),
                ),
            ],
        ]
    )
    task_id = "fork-witness"
    identity = WorktreeIdentity(
        name="agents/fork/witness",
        branch="okcode/agents/agents/fork/witness",
        task_id=task_id,
        parent_session_id="parent",
        role_name=None,
        trigger="tool",
    )
    request = AgentLaunchRequest(
        task_id=task_id,
        kind=AgentLaunchKind.FORK,
        task="修改并提交 witness.txt",
        parent_session_id="parent",
        visible_tool_names=("write_file", "run_command"),
        max_turns=3,
        permission_mode=PermissionMode.ALLOW,
        isolation=AgentIsolationMode.WORKTREE,
        worktree_request=WorktreePrepareRequest(identity, repo),
        main_workspace_root=repo,
    )
    runner = AgentRunner(
        lambda _: provider,
        registry,
        workspace_root=repo,
        worktree_manager=manager,
    )

    result = await runner.run(request, AgentCancelToken())

    assert result.status is AgentTaskStatus.COMPLETED
    assert (repo / "witness.txt").read_text(encoding="utf-8") == (
        "original content from main agent\n"
    )
    assert _git_output(repo, "log", "-1", "--format=%s").strip() == "add witness"
    assert result.worktree is not None
    assert result.worktree.cleanup_decision.value == "kept"
    assert (result.worktree.path / "witness.txt").read_text(encoding="utf-8") == (
        "modified by isolated worker\n"
    )
    assert (
        _git_output(result.worktree.path, "log", "-1", "--format=%s").strip()
        == "isolated witness"
    )
