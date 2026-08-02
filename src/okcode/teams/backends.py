"""团队成员运行后端。"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from okcode.teams.models import (
    BackendCapability,
    BackendPreference,
    BackendSelection,
    MessageDeliveryReport,
    TeamBackendHandle,
    TeamBackendKind,
    TeamMember,
)


class TeamBackendUnavailable(RuntimeError):
    """请求的成员后端在当前环境不可用。"""


@dataclass(frozen=True, slots=True)
class WakeReport:
    """成员唤醒结果。"""

    status: str
    message: str
    woken: bool = False


@dataclass(frozen=True, slots=True)
class TerminateReport:
    """成员终止结果。"""

    status: str
    message: str


class TeamBackend:
    """成员运行后端协议。"""

    kind: TeamBackendKind

    def available(self) -> BackendCapability:
        raise NotImplementedError

    def spawn(self, member: TeamMember, command: tuple[str, ...] = ()) -> TeamBackendHandle:
        raise NotImplementedError

    def wake(self, handle: TeamBackendHandle, message_id: str | None = None) -> WakeReport:
        raise NotImplementedError

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        raise NotImplementedError


class BackendSelector:
    """按显式请求和环境能力选择成员后端。"""

    def __init__(self, priority: tuple[TeamBackendKind, ...] | None = None) -> None:
        self._priority = priority or (
            TeamBackendKind.TERMINAL_PANE,
            TeamBackendKind.COROUTINE,
        )

    def select(
        self,
        preference: BackendPreference,
        backends: tuple[TeamBackend, ...],
    ) -> BackendSelection:
        by_kind = {backend.kind: backend for backend in backends}
        if preference.required_kind is not None:
            return self._required(preference.required_kind, by_kind)
        if preference.require_strong_isolation:
            return self._required(TeamBackendKind.TERMINAL_PANE, by_kind)
        if not preference.allow_auto:
            raise TeamBackendUnavailable("未允许自动选择成员后端，也没有显式指定后端。")
        for kind in self._priority:
            backend = by_kind.get(kind)
            if backend is None:
                continue
            capability = backend.available()
            if capability.available:
                return BackendSelection(kind, "按环境优先级自动选择。", capability)
        details = ", ".join(
            f"{backend.kind.value}:{backend.available().reason}" for backend in backends
        )
        raise TeamBackendUnavailable(f"没有可用成员后端：{details}")

    def _required(
        self,
        kind: TeamBackendKind,
        by_kind: dict[TeamBackendKind, TeamBackend],
    ) -> BackendSelection:
        backend = by_kind.get(kind)
        if backend is None:
            raise TeamBackendUnavailable(f"请求的成员后端未注册：{kind.value}")
        capability = backend.available()
        if not capability.available:
            raise TeamBackendUnavailable(
                f"请求的成员后端不可用：{kind.value}，原因：{capability.reason}"
            )
        return BackendSelection(kind, "满足显式后端请求。", capability)


class TerminalPaneController:
    """终端窗格控制器协议。"""

    def available(self) -> BackendCapability:
        raise NotImplementedError

    def spawn(self, command: tuple[str, ...], cwd: Path, title: str) -> TeamBackendHandle:
        raise NotImplementedError

    def wake(self, handle: TeamBackendHandle) -> WakeReport:
        raise NotImplementedError

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        raise NotImplementedError


class WindowsTerminalPaneController(TerminalPaneController):
    """Windows Terminal 控制器。"""

    def available(self) -> BackendCapability:
        if shutil.which("wt.exe") or shutil.which("wt"):
            return BackendCapability(
                TeamBackendKind.TERMINAL_PANE,
                True,
                "检测到 Windows Terminal。",
            )
        return BackendCapability(TeamBackendKind.TERMINAL_PANE, False, "未找到 wt.exe。")

    def spawn(self, command: tuple[str, ...], cwd: Path, title: str) -> TeamBackendHandle:
        identifier = f"wt-{uuid.uuid4().hex[:8]}"
        return TeamBackendHandle(
            TeamBackendKind.TERMINAL_PANE,
            identifier,
            cwd,
            {"title": title, "command": list(command), "controller": "windows_terminal"},
        )

    def wake(self, handle: TeamBackendHandle) -> WakeReport:
        return WakeReport("woken", f"已请求唤醒终端窗格：{handle.identifier}", True)

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        return TerminateReport("terminated", f"已请求终止终端窗格：{handle.identifier}")


class TmuxPaneController(TerminalPaneController):
    """tmux 控制器。"""

    def available(self) -> BackendCapability:
        if shutil.which("tmux"):
            return BackendCapability(TeamBackendKind.TERMINAL_PANE, True, "检测到 tmux。")
        return BackendCapability(TeamBackendKind.TERMINAL_PANE, False, "未找到 tmux。")

    def spawn(self, command: tuple[str, ...], cwd: Path, title: str) -> TeamBackendHandle:
        identifier = f"tmux-{uuid.uuid4().hex[:8]}"
        return TeamBackendHandle(
            TeamBackendKind.TERMINAL_PANE,
            identifier,
            cwd,
            {"title": title, "command": list(command), "controller": "tmux"},
        )

    def wake(self, handle: TeamBackendHandle) -> WakeReport:
        return WakeReport("woken", f"已请求唤醒 tmux 窗格：{handle.identifier}", True)

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        return TerminateReport("terminated", f"已请求终止 tmux 窗格：{handle.identifier}")


class TerminalPaneBackend(TeamBackend):
    """独立终端窗格后端。"""

    kind = TeamBackendKind.TERMINAL_PANE

    def __init__(self, controller: TerminalPaneController | None = None) -> None:
        self._controller = controller or WindowsTerminalPaneController()

    def available(self) -> BackendCapability:
        return self._controller.available()

    def spawn(self, member: TeamMember, command: tuple[str, ...] = ()) -> TeamBackendHandle:
        return self._controller.spawn(command, member.workdir, f"OkCode team:{member.name}")

    def wake(self, handle: TeamBackendHandle, message_id: str | None = None) -> WakeReport:
        return self._controller.wake(handle)

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        return self._controller.terminate(handle)


class CoroutineBackend(TeamBackend):
    """同进程协程成员后端。"""

    kind = TeamBackendKind.COROUTINE

    def __init__(self) -> None:
        self._queued: dict[str, asyncio.Event] = {}

    def available(self) -> BackendCapability:
        return BackendCapability(self.kind, True, "同进程协程后端始终可用。")

    def spawn(self, member: TeamMember, command: tuple[str, ...] = ()) -> TeamBackendHandle:
        identifier = f"coroutine-{uuid.uuid4().hex[:8]}"
        self._queued[identifier] = asyncio.Event()
        return TeamBackendHandle(self.kind, identifier, member.workdir, {"command": list(command)})

    def wake(self, handle: TeamBackendHandle, message_id: str | None = None) -> WakeReport:
        event = self._queued.setdefault(handle.identifier, asyncio.Event())
        event.set()
        return WakeReport("woken", f"已唤醒协程成员：{handle.identifier}", True)

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        self._queued.pop(handle.identifier, None)
        return TerminateReport("terminated", f"已终止协程成员：{handle.identifier}")


def delivery_from_wake(
    recipient: str,
    message_id: str,
    mailbox_path: Path,
    wake: WakeReport,
) -> MessageDeliveryReport:
    """把唤醒结果转换成消息投递报告。"""

    if wake.woken:
        return MessageDeliveryReport(
            recipient=recipient,
            status="delivered",
            message_id=message_id,
            mailbox_path=mailbox_path,
            woken=True,
        )
    return MessageDeliveryReport(
        recipient=recipient,
        status="written_but_not_woken",
        message_id=message_id,
        mailbox_path=mailbox_path,
        error=wake.message,
        woken=False,
    )
