"""Hook 动作执行器。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from okcode.hooks.models import (
    HookContext,
    HookInterception,
    HookRule,
    HttpHookAction,
    PromptHookAction,
    PromptScope,
    ShellHookAction,
    SubAgentHookAction,
)
from okcode.permissions.manager import PermissionManager
from okcode.tools.workspace import Workspace

_LOG = logging.getLogger(__name__)
_STREAM_LIMIT = 6_000


@dataclass(frozen=True, slots=True)
class ShellCommandResult:
    """一次 Hook shell 命令的脱敏执行结果。"""

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class HookActionOutcome:
    """动作执行后交给 HookRuntime 处理的结果。"""

    status: str
    message: str = ""
    interception: HookInterception | None = None
    prompt_content: str | None = None
    prompt_scope: PromptScope | None = None


ShellRunner = Callable[[str, Path, str, float], Awaitable[ShellCommandResult]]


class HookActionRunner:
    """执行四类 Hook 动作，并保证失败不越过 Hook 边界。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        permissions: PermissionManager | None = None,
        shell_runner: ShellRunner | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._workspace = workspace
        self._permissions = permissions
        self._shell_runner = shell_runner or _run_shell_command
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    async def run(self, rule: HookRule, context: HookContext) -> HookActionOutcome:
        """执行单条规则动作。"""

        action = rule.action
        if isinstance(action, ShellHookAction):
            return await self._run_shell(rule, context, action)
        if isinstance(action, PromptHookAction):
            return HookActionOutcome(
                "prompt",
                "提示词已加入队列。",
                prompt_content=action.content,
                prompt_scope=action.scope,
            )
        if isinstance(action, HttpHookAction):
            return await self._run_http(rule, context, action)
        if isinstance(action, SubAgentHookAction):
            return HookActionOutcome(
                "subagent_skipped",
                f"子 Agent 动作已跳过，等待 SubAgent 阶段对接：{action.task[:80]}",
            )
        return HookActionOutcome("skipped", "未知 Hook 动作，已跳过。")

    async def _run_shell(
        self,
        rule: HookRule,
        context: HookContext,
        action: ShellHookAction,
    ) -> HookActionOutcome:
        if self._permissions is not None:
            decision = await self._permissions.authorize_hook_command_async(
                action.command,
                background=rule.control.background,
            )
            if not decision.allowed:
                return HookActionOutcome("permission_denied", decision.reason)
        cwd = self._resolve_cwd(action.cwd)
        payload = json.dumps(
            {"event": context.event.value, "values": context.values},
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            result = await self._shell_runner(
                action.command,
                cwd,
                payload,
                rule.control.timeout_seconds,
            )
        except Exception as exc:
            _LOG.info("Hook shell 执行失败：%s", exc)
            return HookActionOutcome("failed", "Hook shell 执行失败。")
        if result.timed_out:
            return HookActionOutcome("timeout", "Hook shell 执行超时。")
        if action.intercept:
            interception = _interception_from_shell(rule, action, result)
            if interception is not None:
                return HookActionOutcome("intercepted", "Hook 已拦截工具调用。", interception)
        status = "ok" if result.exit_code == 0 else "failed"
        return HookActionOutcome(status, f"Hook shell 退出码：{result.exit_code}")

    async def _run_http(
        self,
        rule: HookRule,
        context: HookContext,
        action: HttpHookAction,
    ) -> HookActionOutcome:
        body: Mapping[str, object] = {
            "event": context.event.value,
            "rule": rule.identifier,
            "values": context.values,
        }
        timeout = httpx.Timeout(rule.control.timeout_seconds)
        try:
            async with self._http_client_factory() as client:
                kwargs: dict[str, object] = {
                    "headers": dict(action.headers),
                    "timeout": timeout,
                }
                if action.body is None:
                    kwargs["json"] = body
                elif isinstance(action.body, str):
                    kwargs["content"] = action.body
                else:
                    kwargs["json"] = action.body
                response = await client.request(action.method, action.url, **kwargs)
        except httpx.TimeoutException:
            return HookActionOutcome("timeout", "Hook HTTP 请求超时。")
        except Exception as exc:
            _LOG.info("Hook HTTP 请求失败：%s", exc)
            return HookActionOutcome("failed", "Hook HTTP 请求失败。")
        if 200 <= response.status_code < 300:
            return HookActionOutcome("ok", f"Hook HTTP 状态码：{response.status_code}")
        return HookActionOutcome("failed", f"Hook HTTP 状态码：{response.status_code}")

    def _resolve_cwd(self, raw: str | None) -> Path:
        if raw is None:
            return self._workspace.root
        path, _ = self._workspace.resolve_path_with_relative(raw, must_exist=False)
        return path


def _interception_from_shell(
    rule: HookRule,
    action: ShellHookAction,
    result: ShellCommandResult,
) -> HookInterception | None:
    reason = action.deny_message or "Hook 安全策略拒绝了这次工具调用。"
    if result.exit_code not in (None, 0):
        return HookInterception(reason, rule.identifier)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, Mapping) and payload.get("decision") == "deny":
        message = payload.get("reason")
        if isinstance(message, str) and message.strip() and action.deny_message is None:
            reason = message.strip()
        return HookInterception(reason, rule.identifier)
    return None


async def _run_shell_command(
    command: str,
    cwd: Path,
    stdin_text: str,
    timeout_seconds: float,
) -> ShellCommandResult:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_text.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _terminate_process_tree(process)
        return ShellCommandResult(None, timed_out=True)
    return ShellCommandResult(
        process.returncode,
        _decode_limited(stdout),
        _decode_limited(stderr),
        False,
    )


def _decode_limited(value: bytes) -> str:
    return value[:_STREAM_LIMIT].decode("utf-8", errors="replace")


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    await process.wait()
