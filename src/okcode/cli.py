"""命令行入口与资源生命周期。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from okcode.app import OkCodeApp
from okcode.commands import build_default_command_registry
from okcode.config import load_config
from okcode.context import ArtifactStore, ContextManager
from okcode.conversation import ConversationSession
from okcode.errors import ConfigError
from okcode.instructions import InstructionLoader, InstructionPaths
from okcode.mcp import McpClientManager, McpConfigPaths, load_mcp_config
from okcode.mcp.models import McpDiscoveryWarning
from okcode.memory import MemoryPaths, MemoryStore
from okcode.memory.worker import MemoryWorker
from okcode.permissions import PermissionManager, PermissionPaths, load_permission_rules
from okcode.prompt import PromptCachePolicy, RuntimePromptContextFactory
from okcode.providers.factory import create_provider
from okcode.sessions import SessionStore
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
        paths = PermissionPaths.for_workspace(workspace.root)
        rule_sets = load_permission_rules(paths, known_tool_names)
        permissions = PermissionManager(
            workspace,
            rule_sets,
            paths,
            known_tool_names,
            confirmer=ui.confirm_permission,
        )
        provider = create_provider(config.active_provider)
        conversation = ConversationSession(
            provider,
            registry,
            ToolExecutor(registry, permissions=permissions),
            cache_policy=PromptCachePolicy(enabled=config.active_provider.prompt_cache),
            permissions=permissions,
            context_manager=ContextManager(ArtifactStore(workspace.root)),
            context_factory=RuntimePromptContextFactory(
                workspace.root,
                instructions,
                memory_store,
            ),
            session_store=session_store,
            session_journal=session_store.create_journal(),
            memory_store=memory_store,
            memory_worker=memory_worker,
            model_name=config.active_provider.model,
            workspace_root=workspace.root,
        )
        set_command_registry = getattr(ui, "set_command_registry", None)
        if callable(set_command_registry):
            set_command_registry(command_registry)
        for warning in warnings:
            ui.show_mcp_warning(warning)
        app = OkCodeApp(ui, conversation, runner, config.active_provider, command_registry)
        return app.run()
    except ConfigError as error:
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
        runner.close()
