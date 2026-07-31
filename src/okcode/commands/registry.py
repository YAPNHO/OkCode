"""命令注册中心。"""

from __future__ import annotations

from collections.abc import Iterable

from okcode.commands.models import CommandDefinition, CompletionCandidate


class CommandRegistry:
    """管理命令元数据、别名和补全候选。"""

    def __init__(self, commands: Iterable[CommandDefinition]) -> None:
        self._commands = tuple(commands)
        self._by_key: dict[str, CommandDefinition] = {}
        owners: dict[str, str] = {}
        for command in self._commands:
            keys = (command.name, *command.aliases)
            normalized_keys = tuple(self._normalize(key) for key in keys)
            if not normalized_keys[0]:
                raise ValueError("命令名称不能为空。")
            for key in normalized_keys:
                if not key:
                    raise ValueError(f"命令 {command.name} 包含空别名。")
                previous = owners.get(key)
                if previous is not None:
                    raise ValueError(f"命令键 {key!r} 冲突：{previous} 与 {command.name}")
                owners[key] = command.name
                self._by_key[key] = command

    def resolve(self, name: str) -> CommandDefinition | None:
        return self._by_key.get(self._normalize(name))

    def visible_commands(self) -> tuple[CommandDefinition, ...]:
        return tuple(
            sorted((item for item in self._commands if not item.hidden), key=lambda item: item.name)
        )

    def completion_candidates(self, prefix: str) -> tuple[CompletionCandidate, ...]:
        normalized = self._normalize(prefix.lstrip("/"))
        candidates: list[CompletionCandidate] = []
        for command in self.visible_commands():
            for key in (command.name, *command.aliases):
                if key.startswith(normalized):
                    candidates.append(
                        CompletionCandidate(
                            text="/" + key,
                            display="/" + key,
                            description=command.description,
                        )
                    )
        return tuple(sorted(candidates, key=lambda item: item.text))

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().lstrip("/").lower()
