from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from okcode.hooks.actions import HookActionRunner, ShellCommandResult
from okcode.hooks.models import (
    HookContext,
    HookControl,
    HookEvent,
    HookRule,
    HttpHookAction,
    PromptHookAction,
    ShellHookAction,
    SubAgentHookAction,
)
from okcode.tools.workspace import Workspace


def _rule(action: object, *, control: HookControl | None = None) -> HookRule:
    return HookRule("r1", HookEvent.TOOL_BEFORE, None, action, control or HookControl())


@pytest.mark.asyncio
async def test_shell_action_sends_context_to_runner(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    async def runner(
        command: str, cwd: Path, stdin_text: str, timeout_seconds: float
    ) -> ShellCommandResult:
        seen.update(
            {
                "command": command,
                "cwd": cwd,
                "stdin": stdin_text,
                "timeout": timeout_seconds,
            }
        )
        return ShellCommandResult(0, "ok", "")

    action_runner = HookActionRunner(Workspace(tmp_path), shell_runner=runner)
    outcome = await action_runner.run(
        _rule(ShellHookAction("echo ok"), control=HookControl(timeout_seconds=3)),
        HookContext(HookEvent.TOOL_BEFORE, {"tool.name": "write_file"}),
    )

    assert outcome.status == "ok"
    assert seen["command"] == "echo ok"
    assert seen["cwd"] == tmp_path
    assert '"tool.name": "write_file"' in str(seen["stdin"])
    assert seen["timeout"] == 3


@pytest.mark.asyncio
async def test_shell_intercept_uses_safe_deny_message(tmp_path: Path) -> None:
    async def runner(
        command: str, cwd: Path, stdin_text: str, timeout_seconds: float
    ) -> ShellCommandResult:
        return ShellCommandResult(1, "secret stdout", "secret stderr")

    action_runner = HookActionRunner(Workspace(tmp_path), shell_runner=runner)
    outcome = await action_runner.run(
        _rule(ShellHookAction("exit 1", intercept=True, deny_message="不允许写锁文件")),
        HookContext(HookEvent.TOOL_BEFORE, {"tool.name": "write_file"}),
    )

    assert outcome.interception is not None
    assert outcome.interception.reason == "不允许写锁文件"
    assert "secret" not in outcome.interception.reason


@pytest.mark.asyncio
async def test_shell_intercept_accepts_json_decision(tmp_path: Path) -> None:
    async def runner(
        command: str, cwd: Path, stdin_text: str, timeout_seconds: float
    ) -> ShellCommandResult:
        return ShellCommandResult(0, '{"decision":"deny","reason":"参数不安全"}', "")

    action_runner = HookActionRunner(Workspace(tmp_path), shell_runner=runner)
    outcome = await action_runner.run(
        _rule(ShellHookAction("guard", intercept=True)),
        HookContext(HookEvent.TOOL_BEFORE, {"tool.name": "write_file"}),
    )

    assert outcome.interception is not None
    assert outcome.interception.reason == "参数不安全"


@pytest.mark.asyncio
async def test_http_action_uses_mock_transport(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    action_runner = HookActionRunner(Workspace(tmp_path), http_client_factory=client_factory)
    outcome = await action_runner.run(
        _rule(
            HttpHookAction(
                "https://example.com/hook",
                method="POST",
                headers={"X-Test": "ok"},
                body={"hello": "world"},
            )
        ),
        HookContext(HookEvent.SESSION_START, {"session.id": "s1"}),
    )

    assert outcome.status == "ok"
    assert requests[0].url == "https://example.com/hook"
    assert requests[0].headers["X-Test"] == "ok"
    assert requests[0].content == b'{"hello":"world"}'


@pytest.mark.asyncio
async def test_prompt_and_subagent_actions_have_no_external_side_effects(tmp_path: Path) -> None:
    runner = HookActionRunner(Workspace(tmp_path))

    prompt = await runner.run(
        _rule(PromptHookAction("请补充上下文")),
        HookContext(HookEvent.MESSAGE_USER, {"message.content": "hi"}),
    )
    subagent = await runner.run(
        _rule(SubAgentHookAction("启动检查")),
        HookContext(HookEvent.SESSION_END, {"session.id": "s1"}),
    )

    assert prompt.prompt_content == "请补充上下文"
    assert prompt.prompt_scope is not None
    assert subagent.status == "subagent_skipped"
    assert "等待 SubAgent" in subagent.message
