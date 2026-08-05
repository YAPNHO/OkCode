# OkCode 记忆文件格式与体积感知 Checklist

> 每一项都必须通过代码运行、测试结果或可观察的终端行为验证；不能只依据代码阅读标记完成。

## 实现完整性

- [x] `MemoryPaths` 新索引为 `MEMORY.md`，旧 `index.md` 可回退读取（验证：存储单元测试）。
- [x] `MemoryOperation` 和请求 JSON 使用 `name/summary`（验证：请求解析测试和 Worker 测试）。
- [x] `name` 支持中文和空格，并拒绝路径穿越、Windows 禁止字符、危险结尾、保留设备名及索引保留名（验证：名称校验测试）。
- [x] 新记忆文件 frontmatter 包含 `name`、`scope`、`category`、`summary`、`created_at`、`updated_at`（验证：读取写入文件并解析 YAML）。
- [x] 旧 `id/title` frontmatter 追加后转换为新字段（验证：旧 frontmatter 追加测试）。
- [x] 新索引每行只包含 `[name.md](name.md) | 摘要`，无标题、分类和正文（验证：读取 `MEMORY.md` 逐行断言）。
- [x] 索引仍受 200 行和 25 KB 限制，失败时没有部分写入（验证：超限测试比较写入前后文件内容）。
- [x] `MemoryStore.snapshot()` 统计两个范围下全部当前目录 `.md` 文件的 bytes，不递归子目录（验证：混合扩展名和子目录夹具测试）。

## `/memory` 集成

- [x] `ConversationSession.memory_snapshot()` 使用 `MemoryStore.snapshot()`，不再重复扫描目录（验证：会话快照测试）。
- [x] `CommandMemorySnapshot` 和 `CommandMemory` 传递项目/用户 bytes（验证：命令处理测试）。
- [x] `/memory` 输出保留两个文件列表，并新增项目大小、用户大小、总大小（验证：终端纯文本输出）。
- [x] 终端将 bytes 按 1024 转为两位小数 `KiB`，空目录显示 `0.00 KiB`（验证：终端格式化测试）。
- [x] 总大小等于项目 bytes 与用户 bytes 之和（验证：命令事件和终端输出断言）。

## 兼容与回归

- [x] 只有旧 `index.md` 时，长期记忆上下文仍能读取（验证：兼容读取测试）。
- [x] 有 `MEMORY.md` 和 `index.md` 时，只读取 `MEMORY.md`，不重复注入（验证：优先级测试）。
- [x] 旧记忆追加成功并生成新格式 frontmatter，旧索引文件不被删除（验证：追加后的文件内容和路径断言）。
- [x] 普通对话、后台 MemoryWorker、工具调用、权限、MCP 和上下文压缩行为不变（验证：相关现有测试集）。
- [x] 无网络替身测试覆盖 create、append、noop 和失败隔离（验证：MemoryRequest/Worker 测试）。

## 测试与质量

- [x] 存储、请求、Worker、会话、命令和终端定向测试全部通过（验证：`uv run pytest` 定向命令，88 passed）。
- [x] 至少一个端到端场景完成“写入记忆 -> 获取 `/memory` 快照 -> 渲染大小”（验证：终端跨模块测试）。
- [x] `uv run pytest -q` 通过（验证：465 passed，命令退出码为 0）。
- [x] `uv run ruff check .` 通过（验证：命令退出码为 0）。
- [x] `git diff --check` 通过（验证：无空白错误；Git 仅提示换行符转换）。
- [x] 代码和新增注释保持简体中文，未引入无关文件修改（验证：`git diff` 人工检查）。
