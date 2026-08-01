"""命令行入口与资源生命周期。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from okcode.agents import (
    AGENT_TOOL_NAME,
    AgentLauncher,
    AgentRolePaths,
    AgentRunner,
    AgentTaskManager,
    AgentTool,
    load_agent_roles,
)
from okcode.app import OkCodeApp
from okcode.commands import build_default_command_registry
from okcode.config import load_config
from okcode.context import ArtifactStore, ContextManager
from okcode.conversation import ConversationSession
from okcode.errors import ConfigError
from okcode.hooks import HookPaths, HookRuntime, load_hook_rules
from okcode.hooks.actions import HookActionRunner
from okcode.instructions import InstructionLoader, InstructionPaths
from okcode.mcp import McpClientManager, McpConfigPaths, load_mcp_config
from okcode.mcp.models import McpDiscoveryWarning
from okcode.memory import MemoryPaths, MemoryStore
from okcode.memory.worker import MemoryWorker
from okcode.models import ChatMessage
from okcode.permissions import PermissionManager, PermissionPaths, load_permission_rules
from okcode.prompt import PromptCachePolicy, RuntimePromptContextFactory
from okcode.providers.factory import create_provider
from okcode.sessions import SessionStore
from okcode.skills import (
    LOAD_SKILL_TOOL_NAME,
    LoadSkillTool,
    SkillActivationStore,
    SkillCatalog,
    SkillRoots,
    SkillRunner,
    SkillRuntime,
    SkillValidationError,
)
from okcode.skills.discovery import dedicated_tool_names
from okcode.terminal import TerminalUI
from okcode.tools.defaults import build_default_registry
from okcode.tools.executor import ToolExecutor
from okcode.tools.workspace import Workspace


def main() -> int:
    """启动 OkCode 并返回进程退出码。"""

    ui = TerminalUI()
    try:
        config = load_config()
    except ConfigError as error:
        ui.show_config_error(str(error))
        return 2

    runner = asyncio.Runner()
    provider = None
    mcp_manager = None
    memory_worker = None
    agent_task_manager = None
    hooks = None
    try:
        workspace = Workspace(Path.cwd())
        session_store = SessionStore(workspace.root)
        session_store.cleanup_expired()
        instructions = InstructionLoader(
            InstructionPaths.for_workspace(workspace.root), workspace.root
        ).load()
        memory_store = MemoryStore(MemoryPaths.for_workspace(workspace.root))
        command_registry = build_default_command_registry()
        memory_worker = MemoryWorker(
            lambda: create_provider(config.active_provider),
            memory_store,
        )
        registry = build_default_registry(workspace)
        mcp_config = load_mcp_config(McpConfigPaths.for_workspace(workspace.root))
        mcp_manager = McpClientManager(mcp_config.servers)
        discovery = runner.run(mcp_manager.discover_tools())
        warnings = list(discovery.warnings)
        for tool in discovery.tools:
            try:
                registry.register(tool)
            except ValueError:
                warnings.append(
                    McpDiscoveryWarning(
                        tool.server_name,
                        "工具注册",
                        "MCP 工具名称与已有工具冲突，已跳过。",
                    )
                )
        known_tool_names = {definition.name for definition in registry.definitions()}
        known_tool_names.add(AGENT_TOOL_NAME)
        skill_catalog = SkillCatalog(SkillRoots.for_workspace(workspace.root), known_tool_names)
        skill_runtime = SkillRuntime(
            skill_catalog,
            SkillActivationStore(),
            command_registry,
        )
        skill_runtime.refresh()
        permission_tool_names = {
            *known_tool_names,
            LOAD_SKILL_TOOL_NAME,
            *dedicated_tool_names(skill_catalog.list()),
        }
        paths = PermissionPaths.for_workspace(workspace.root)
        rule_sets = load_permission_rules(paths, permission_tool_names)
        permissions = PermissionManager(
            workspace,
            rule_sets,
            paths,
            permission_tool_names,
            confirmer=ui.confirm_permission,
        )
        provider = create_provider(config.active_provider)
        context_manager = ContextManager(ArtifactStore(workspace.root))

        def child_provider_factory(model_override: str | None = None):
            provider_config = config.active_provider
            if model_override:
                provider_config = replace(provider_config, model=model_override)
            return create_provider(provider_config)

        agent_roles = load_agent_roles(AgentRolePaths.for_workspace(workspace.root))
        agent_runner = AgentRunner(
            child_provider_factory,
            registry,
            workspace_root=workspace.root,
            cache_policy=PromptCachePolicy(enabled=config.active_provider.prompt_cache),
            parent_permissions=permissions,
        )
        agent_task_manager = AgentTaskManager(agent_runner)
        agent_launcher = AgentLauncher(agent_roles, registry, agent_task_manager)

        def parent_agent_context():
            return conversation.parent_agent_context(registry.definitions())

        hook_paths = HookPaths.for_workspace(workspace.root)
        hook_rules = load_hook_rules(hook_paths)
        hooks = HookRuntime(
            hook_rules,
            runner=HookActionRunner(
                workspace,
                permissions=permissions,
                agent_launcher=agent_launcher,
                parent_context_provider=parent_agent_context,
            ),
            config_path=str(hook_paths.config),
        )
        executor = ToolExecutor(registry, permissions=permissions, hooks=hooks)
        runner_for_skill = SkillRunner(
            provider,
            cache_policy=PromptCachePolicy(enabled=config.active_provider.prompt_cache),
            executor=executor,
        )

        def active_history() -> tuple[ChatMessage, ...]:
            return conversation.messages

        def context_summary() -> str | None:
            return context_manager.state.summary

        registry.register(AgentTool(agent_launcher, parent_agent_context))
        registry.register(
            LoadSkillTool(
                skill_catalog,
                skill_runtime.activation_store,
                registry,
                runner=runner_for_skill,
                history_provider=active_history,
                history_summary_provider=context_summary,
                refresh_callback=skill_runtime.refresh,
            )
        )
        conversation = ConversationSession(
            provider,
            registry,
            executor,
            cache_policy=PromptCachePolicy(enabled=config.active_provider.prompt_cache),
            permissions=permissions,
            context_manager=context_manager,
            context_factory=RuntimePromptContextFactory(
                workspace.root,
                instructions,
                memory_store,
                available_skills_provider=skill_runtime.render_available_section,
                active_skills_provider=skill_runtime.render_active_section,
            ),
            session_store=session_store,
            session_journal=session_store.create_journal(),
            memory_store=memory_store,
            memory_worker=memory_worker,
            model_name=config.active_provider.model,
            workspace_root=workspace.root,
            skill_runtime=skill_runtime,
            hooks=hooks,
            agent_task_manager=agent_task_manager,
        )
        set_command_registry = getattr(ui, "set_command_registry", None)
        if callable(set_command_registry):
            set_command_registry(command_registry)
        for warning in warnings:
            ui.show_mcp_warning(warning)
        app = OkCodeApp(
            ui,
            conversation,
            runner,
            config.active_provider,
            command_registry,
            skill_runtime,
            hooks,
        )
        return app.run()
    except (ConfigError, SkillValidationError) as error:
        ui.show_config_error(str(error))
        return 2
    except Exception:
        ui.show_startup_error()
        return 1
    finally:
        if memory_worker is not None:
            try:
                memory_worker.close()
            except Exception:
                pass
        if agent_task_manager is not None:
            try:
                agent_task_manager.close()
            except Exception:
                pass
        if mcp_manager is not None:
            try:
                runner.run(mcp_manager.aclose())
            except Exception:
                pass
        if provider is not None:
            try:
                runner.run(provider.aclose())
            except Exception:
                pass
        if hooks is not None:
            try:
                runner.run(hooks.aclose())
            except Exception:
                pass
        runner.close()
