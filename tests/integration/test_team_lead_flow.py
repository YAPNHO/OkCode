from __future__ import annotations

from pathlib import Path

import pytest

from okcode.conversation import ConversationSession
from okcode.models import (
    AppConfig,
    ChatMessage,
    ProviderConfig,
    ProviderProtocol,
    Role,
    StreamCompleted,
    TeamFeatureConfig,
    TeamStatusEvent,
    ToolCall,
)
from okcode.sessions import SessionStore
from okcode.teams.backends import BackendSelector, CoroutineBackend
from okcode.teams.mailbox import MailboxStore
from okcode.teams.models import (
    BackendPreference,
    TeamBackendKind,
    TeamMessage,
    TeamMessageProtocol,
)
from okcode.teams.runtime import TeamRuntime
from okcode.teams.store import TeamStore
from okcode.teams.worker import TeamWorkerApp
from okcode.tools.defaults import build_default_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.workspace import Workspace
from tests.fakes import FakeProvider


def _runtime(tmp_path: Path) -> TeamRuntime:
    return TeamRuntime(
        store=TeamStore(tmp_path / "teams", lock_timeout_seconds=0.2, stale_lock_seconds=0.2),
        mailbox=MailboxStore(lock_timeout_seconds=0.2, stale_lock_seconds=0.2),
        selector=BackendSelector((TeamBackendKind.COROUTINE,)),
        backends=(CoroutineBackend(),),
    )


def _app_config(*, coordinator_enabled: bool) -> AppConfig:
    return AppConfig(
        active="test",
        providers=(
            ProviderConfig(
                name="test",
                protocol=ProviderProtocol.OPENAI,
                model="model",
                base_url="https://example.com",
                api_key="secret",
            ),
        ),
        team=TeamFeatureConfig(coordinator_enabled=coordinator_enabled),
    )


def _session(
    tmp_path: Path,
    runtime: TeamRuntime,
    *,
    app_config: AppConfig | None = None,
    provider: FakeProvider | None = None,
) -> ConversationSession:
    tmp_path.mkdir(parents=True, exist_ok=True)
    registry = build_default_registry(Workspace(tmp_path))
    session_store = SessionStore(tmp_path)
    return ConversationSession(
        provider or FakeProvider([]),
        registry,
        ToolExecutor(registry),
        session_store=session_store,
        session_journal=session_store.create_journal(),
        workspace_root=tmp_path,
        app_config=app_config,
        team_runtime=runtime,
    )


def test_team_lead_flow_creates_team_and_exposes_shared_state(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    session = _session(tmp_path, runtime)

    before = session.status_snapshot().available_tool_count
    created = session.create_team("core")

    assert isinstance(created, TeamStatusEvent)
    assert created.team_name == "core"
    assert session.status_snapshot().available_tool_count > before

    runtime.add_member(
        "core",
        name="worker",
        role="builder",
        workdir=tmp_path,
        backend_preference=BackendPreference(required_kind=TeamBackendKind.COROUTINE),
    )
    task = runtime.create_task(
        "core",
        title="build",
        body="implement feature",
        owner="worker",
        dependencies=("task-prerequisite",),
    )
    delivery = runtime.send_message(
        "core",
        "lead",
        "worker",
        TeamMessage(
            "lead",
            "worker",
            "please start",
            protocol=TeamMessageProtocol.TASK_ASSIGNMENT,
            task_id=task.task_id,
        ),
    )

    status = session.team_status_event()
    assert isinstance(status, TeamStatusEvent)
    assert status.task_count == 1
    assert status.blocked_task_count == 0
    assert delivery.status == "delivered"
    assert status.members[0].name == "worker"
    assert status.members[0].unread_count == 1
    assert status.members[0].recoverable is False


def test_coordinator_double_lock_changes_visible_team_tool_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OKCODE_COORDINATOR", "1")

    normal_runtime = _runtime(tmp_path / "normal")
    normal_session = _session(tmp_path / "normal", normal_runtime)
    normal_session.create_team("core")
    normal_count = normal_session.status_snapshot().available_tool_count

    coordinator_runtime = _runtime(tmp_path / "coordinator")
    coordinator_session = _session(
        tmp_path / "coordinator",
        coordinator_runtime,
        app_config=_app_config(coordinator_enabled=True),
    )
    coordinator_session.create_team("core")
    coordinator_count = coordinator_session.status_snapshot().available_tool_count

    assert coordinator_count < normal_count


@pytest.mark.asyncio
async def test_team_member_worker_reads_file_and_reports_completion(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# OkCode\n\n## 主要能力\n支持团队协作。\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            [
                StreamCompleted(
                    ChatMessage(
                        Role.ASSISTANT,
                        tool_call=ToolCall(
                            "read-1",
                            "read_file",
                            '{"path":"AGENTS.md"}',
                        ),
                    )
                )
            ],
            [
                StreamCompleted(
                    ChatMessage(Role.ASSISTANT, "AGENTS.md 包含项目能力章节，介绍了团队协作。")
                )
            ],
        ]
    )
    runtime = _runtime(tmp_path)
    registry = build_default_registry(Workspace(tmp_path))

    async def worker_factory(team_name: str, member_name: str, message_id: str | None) -> None:
        member = next(
            item for item in runtime.snapshot(team_name).members if item.name == member_name
        )
        await TeamWorkerApp(
            runtime,
            team_name,
            member_name,
            member.workdir,
            provider_factory=lambda _: provider,
            registry=registry,
        ).run_once_async(message_id)

    runtime.configure_worker_factory(worker_factory)
    runtime.create_team("demo", "lead-session")
    runtime.add_member(
        "demo",
        name="alice",
        role="reader",
        workdir=tmp_path,
        backend_preference=BackendPreference(required_kind=TeamBackendKind.COROUTINE),
    )
    task = runtime.create_task(
        "demo",
        title="总结 AGENTS.md",
        body="读取 AGENTS.md 并总结主要章节。",
        owner="alice",
    )
    delivery = runtime.send_message(
        "demo",
        "lead",
        "alice",
        TeamMessage(
            "lead",
            "alice",
            "开始执行",
            protocol=TeamMessageProtocol.TASK_ASSIGNMENT,
            task_id=task.task_id,
        ),
    )
    assert delivery.status == "delivered"

    completed = await runtime.wake_member_async("demo", "alice")

    snapshot = runtime.snapshot("demo")
    lead = runtime.store.read_registry("demo").get("lead")
    assert completed.status == "idle"
    assert snapshot.tasks[0].status.value == "done"
    assert snapshot.tasks[0].output_summary is not None
    assert snapshot.members[0].status.value == "idle"
    assert snapshot.members[0].context_ref is not None
    assert snapshot.members[0].context_ref.journal_path.exists()
    assert lead is not None
    notifications = runtime.mailbox.unread(lead.mailbox_path)
    assert len(notifications) == 1
    assert notifications[0].protocol is TeamMessageProtocol.COMPLETION
    assert "AGENTS.md" in notifications[0].body

    lead_provider = FakeProvider(
        [StreamCompleted(ChatMessage(Role.ASSISTANT, "已汇总 alice 的结果。"))]
    )
    lead_session = _session(
        tmp_path,
        runtime,
        app_config=_app_config(coordinator_enabled=False),
        provider=lead_provider,
    )
    lead_session.use_team("demo")
    _ = [event async for event in lead_session.stream_user_message("汇总团队结果")]
    dynamic = lead_provider.provider_requests[0].prompt.dynamic_system
    assert any("AGENTS.md" in item.content for item in dynamic if item.kind == "team_messages")
