"""命令行入口与资源生命周期。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from okcode.app import OkCodeApp
from okcode.config import load_config
from okcode.conversation import ConversationSession
from okcode.errors import ConfigError
from okcode.permissions import PermissionManager, PermissionPaths, load_permission_rules
from okcode.prompt import PromptCachePolicy
from okcode.providers.factory import create_provider
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
    try:
        workspace = Workspace(Path.cwd())
        registry = build_default_registry(workspace)
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
        )
        app = OkCodeApp(ui, conversation, runner, config.active_provider)
        return app.run()
    except ConfigError as error:
        ui.show_config_error(str(error))
        return 2
    except Exception:
        ui.show_startup_error()
        return 1
    finally:
        if provider is not None:
            try:
                runner.run(provider.aclose())
            except Exception:
                pass
        runner.close()
