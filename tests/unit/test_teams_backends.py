from __future__ import annotations

from pathlib import Path

import pytest

from okcode.teams.backends import (
    BackendSelector,
    CoroutineBackend,
    TeamBackend,
    TeamBackendUnavailable,
    TerminalPaneBackend,
    TerminateReport,
    WakeReport,
    delivery_from_wake,
)
from okcode.teams.models import (
    BackendCapability,
    BackendPreference,
    TeamBackendHandle,
    TeamBackendKind,
    TeamMember,
)


class FakeBackend(TeamBackend):
    def __init__(
        self,
        kind: TeamBackendKind,
        *,
        available: bool = True,
        wake_report: WakeReport | None = None,
    ) -> None:
        self.kind = kind
        self._available = available
        self._wake_report = wake_report or WakeReport("woken", "ok", True)

    def available(self) -> BackendCapability:
        return BackendCapability(self.kind, self._available, "fake")

    def spawn(self, member: TeamMember, command: tuple[str, ...] = ()) -> TeamBackendHandle:
        return TeamBackendHandle(self.kind, f"{self.kind.value}-1", member.workdir)

    def wake(self, handle: TeamBackendHandle, message_id: str | None = None) -> WakeReport:
        return self._wake_report

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        return TerminateReport("terminated", "done")


class FakeController:
    def __init__(self, *, available: bool = True, wake_report: WakeReport | None = None) -> None:
        self._available = available
        self._wake_report = wake_report or WakeReport("woken", "ok", True)

    def available(self) -> BackendCapability:
        return BackendCapability(TeamBackendKind.TERMINAL_PANE, self._available, "fake")

    def spawn(self, command: tuple[str, ...], cwd: Path, title: str) -> TeamBackendHandle:
        return TeamBackendHandle(
            TeamBackendKind.TERMINAL_PANE,
            "pane-1",
            cwd,
            {"title": title, "command": list(command)},
        )

    def wake(self, handle: TeamBackendHandle) -> WakeReport:
        return self._wake_report

    def terminate(self, handle: TeamBackendHandle) -> TerminateReport:
        return TerminateReport("terminated", handle.identifier)


def _member(tmp_path: Path) -> TeamMember:
    return TeamMember(
        "worker",
        "builder",
        tmp_path,
        TeamBackendKind.COROUTINE,
        tmp_path / "mailbox.jsonl",
    )


def test_backend_selector_respects_required_and_strong_isolation(tmp_path: Path) -> None:
    selector = BackendSelector()
    terminal = FakeBackend(TeamBackendKind.TERMINAL_PANE)
    coroutine = FakeBackend(TeamBackendKind.COROUTINE)

    selected = selector.select(
        BackendPreference(required_kind=TeamBackendKind.COROUTINE),
        (terminal, coroutine),
    )
    strong = selector.select(BackendPreference(require_strong_isolation=True), (terminal,))

    assert selected.kind is TeamBackendKind.COROUTINE
    assert strong.kind is TeamBackendKind.TERMINAL_PANE
    with pytest.raises(TeamBackendUnavailable, match="不可用"):
        selector.select(
            BackendPreference(required_kind=TeamBackendKind.TERMINAL_PANE),
            (FakeBackend(TeamBackendKind.TERMINAL_PANE, available=False),),
        )


def test_backend_selector_auto_does_not_silently_downgrade() -> None:
    selector = BackendSelector((TeamBackendKind.TERMINAL_PANE, TeamBackendKind.COROUTINE))

    selected = selector.select(
        BackendPreference(),
        (
            FakeBackend(TeamBackendKind.TERMINAL_PANE, available=False),
            FakeBackend(TeamBackendKind.COROUTINE),
        ),
    )

    assert selected.kind is TeamBackendKind.COROUTINE
    with pytest.raises(TeamBackendUnavailable, match="未允许自动选择"):
        selector.select(BackendPreference(allow_auto=False), ())


def test_terminal_pane_backend_delegates_to_controller(tmp_path: Path) -> None:
    backend = TerminalPaneBackend(FakeController())
    handle = backend.spawn(_member(tmp_path), ("okcode", "team-worker"))

    assert handle.kind is TeamBackendKind.TERMINAL_PANE
    assert handle.cwd == tmp_path
    assert backend.wake(handle).woken is True
    assert backend.terminate(handle).status == "terminated"


def test_coroutine_backend_returns_stable_handle_and_wakes(tmp_path: Path) -> None:
    backend = CoroutineBackend()
    handle = backend.spawn(_member(tmp_path))

    assert handle.kind is TeamBackendKind.COROUTINE
    assert backend.wake(handle).woken is True
    assert backend.terminate(handle).status == "terminated"


def test_delivery_report_keeps_written_message_when_wake_fails(tmp_path: Path) -> None:
    report = delivery_from_wake(
        "worker",
        "msg-1",
        tmp_path / "box.jsonl",
        WakeReport("failed", "pane missing", False),
    )

    assert report.status == "written_but_not_woken"
    assert report.message_id == "msg-1"
    assert report.error == "pane missing"
