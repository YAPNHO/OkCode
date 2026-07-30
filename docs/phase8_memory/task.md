# OkCode 第八阶段：会话恢复与长期记忆 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `src/okcode/instructions.py` | 三层指令和安全 `@include` |
| 新建 | `src/okcode/sessions/__init__.py` | 会话存档公开接口 |
| 新建 | `src/okcode/sessions/models.py` | 会话配置、摘要和恢复结果 |
| 新建 | `src/okcode/sessions/codec.py` | 消息 JSONL 编解码与配对校验 |
| 新建 | `src/okcode/sessions/store.py` | 日志追加、扫描、恢复与清理 |
| 新建 | `src/okcode/memory/__init__.py` | 长期记忆公开接口 |
| 新建 | `src/okcode/memory/models.py` | 记忆范围、分类、任务和更新模型 |
| 新建 | `src/okcode/memory/store.py` | Markdown/frontmatter 和受限索引 |
| 新建 | `src/okcode/memory/request.py` | 无工具记忆请求与 JSON 解析 |
| 新建 | `src/okcode/memory/worker.py` | 串行后台 Worker |
| 新建 | `src/okcode/prompt/runtime.py` | 每轮运行时提示词上下文 |
| 修改 | `src/okcode/context/manager.py` | 恢复历史初始化 |
| 修改 | `src/okcode/models.py` | 恢复失败可观察状态 |
| 修改 | `src/okcode/conversation.py` | 会话存档、恢复、记忆投递和时间提醒 |
| 修改 | `src/okcode/terminal.py` | 会话列表和编号选择 |
| 修改 | `src/okcode/app.py` | `/resume` 应用层编排 |
| 修改 | `src/okcode/cli.py` | 服务装配和退出清理 |
| 修改 | `README.md` | 第八阶段使用说明 |
| 新建/修改 | `tests/unit/test_*.py` | 新模块与接入行为的离线测试 |

## T1：实现分层指令加载

**文件：** `src/okcode/instructions.py`  
**依赖：** 无

**步骤：**
1. 定义三层 `AGENTS.md` 的固定路径模型及最高优先级到最低优先级的读取顺序。
2. 解析独占一行的 `@include`，以工作区根目录解析目标。
3. 用解析后路径的 visited 集合、深度上限和工作区边界拦截循环及越界引用。
4. 缺失顶层文件返回空内容，引用错误包含来源文件和行号。

**验证：** 新增 `tests/unit/test_instructions.py`，运行 `uv run pytest tests/unit/test_instructions.py -q`。

## T2：定义会话数据模型

**文件：** `src/okcode/sessions/models.py`、`src/okcode/sessions/__init__.py`  
**依赖：** 无

**步骤：**
1. 定义保留期、长时间间隔、列表摘要和恢复结果的数据类。
2. 定义格式验证的会话 ID 和用于存档的 UTC 时间边界。
3. 从包根导出稳定的公开类型。

**验证：** 运行 `uv run pytest tests/unit/test_sessions.py -q` 中的数据模型用例。

## T3：实现会话消息 JSONL 编解码

**文件：** `src/okcode/sessions/codec.py`  
**依赖：** T2

**步骤：**
1. 将 `ChatMessage`、工具调用、工具结果和 JSON 型 `provider_state` 转换为稳定 JSON 对象。
2. 严格验证恢复输入的字段、角色和错误码，并重建领域消息。
3. 实现工具调用与结果的完整前缀校验，支持一条助手消息的多个调用与一条工具消息的多个结果。

**验证：** 运行 `uv run pytest tests/unit/test_sessions.py -q`，覆盖 OpenAI 与 Anthropic 状态往返、错配和重复调用 ID。

## T4：实现 JSONL 会话日志追加

**文件：** `src/okcode/sessions/store.py`  
**依赖：** T2、T3

**步骤：**
1. 生成 `YYYYMMDD-HHMMSS-xxxx` ID，并实现惰性 `SessionJournal` 的逐行追加和刷新。
2. 为每条已提交消息写入 UTC 时间戳和稳定 JSON，确保首轮成功前不创建空日志文件。
3. 限制日志路径只能位于当前项目的 `sessions/` 目录。

**验证：** 运行 `uv run pytest tests/unit/test_sessions.py -q`，覆盖 ID 格式、惰性创建、追加顺序和完整行写入。

## T5：实现会话扫描、恢复和清理

**文件：** `src/okcode/sessions/store.py`、`tests/unit/test_sessions.py`  
**依赖：** T3、T4

**步骤：**
1. 扫描 JSONL 直接计算首条用户消息标题、消息数和最后活动时间，并按最近时间排序。
2. 恢复时跳过坏行、以 T3 的校验器截断不完整工具前缀，并报告跳过行数和截断状态。
3. 验证选择的 ID 只能定位当前项目日志；按 30 天保留期清理过期文件。

**验证：** 运行 `uv run pytest tests/unit/test_sessions.py -q`，覆盖半行崩溃、坏行、摘要扫描、截断和清理。

## T6：定义长期记忆模型和存储路径

**文件：** `src/okcode/memory/models.py`、`src/okcode/memory/__init__.py`  
**依赖：** 无

**步骤：**
1. 定义用户/项目范围、四类笔记、任务、操作和完整更新模型。
2. 定义项目与用户 `memory/` 根目录及各自索引位置。
3. 从包根导出接入层需要的稳定类型。

**验证：** 运行 `uv run pytest tests/unit/test_memory_store.py -q` 中的模型与路径用例。

## T7：实现 Markdown 笔记与索引存储

**文件：** `src/okcode/memory/store.py`  
**依赖：** T6

**步骤：**
1. 创建带 frontmatter 的 Markdown 笔记，更新时只向既有笔记正文追加内容。
2. 读取并拼接双范围索引，供提示词层直接注入。
3. 校验候选索引的引用、行数和 UTF-8 字节数；以临时文件替换保证单文件写入完整。
4. 先落盘新笔记和更新笔记，再替换对应索引，失败时不写入不合法索引。

**验证：** 运行 `uv run pytest tests/unit/test_memory_store.py -q`，覆盖双目录、frontmatter、无效引用和两种上限。

## T8：实现记忆 LLM 请求和受控响应解析

**文件：** `src/okcode/memory/request.py`  
**依赖：** T6、T7

**步骤：**
1. 用本轮消息和现有双索引构建无工具、无缓存的 `ProviderRequest`。
2. 在系统提示中要求四类分类、LLM 去重判断和唯一 JSON 正式输出。
3. 严格解析操作、范围、笔记引用和完整候选索引，拒绝工具调用、空回答和未知字段。

**验证：** 新增 `tests/unit/test_memory_request.py`，运行 `uv run pytest tests/unit/test_memory_request.py -q`。

## T9：实现串行后台记忆 Worker

**文件：** `src/okcode/memory/worker.py`  
**依赖：** T7、T8

**步骤：**
1. 用 `queue.Queue` 和 daemon 线程实现 `submit()`、后台顺序消费及有限等待的 `close()`。
2. 在线程事件循环内通过注入的工厂创建 Provider，消费一个且仅一个完成事件。
3. 将成功响应交给解析器和 Store；隔离 Provider、解析、写入异常并继续处理后续任务。

**验证：** 新增 `tests/unit/test_memory_worker.py`，运行 `uv run pytest tests/unit/test_memory_worker.py -q`，覆盖串行、失败隔离和关闭。

## T10：实现运行时提示词上下文来源

**文件：** `src/okcode/prompt/runtime.py`、`src/okcode/prompt/__init__.py`  
**依赖：** T1、T7

**步骤：**
1. 定义可调用的运行时上下文工厂，持有工作区、加载后的指令和 `MemoryStore`。
2. 每次调用时读取当前索引，并填入 `PromptOptionalSections.custom_instructions` 与 `long_term_memory`。
3. 导出工厂并保持现有 `PromptBuilder` 的优先级、缓存和稳定提示不变。

**验证：** 扩展 `tests/unit/test_prompt_builder.py` 或新增运行时上下文测试，运行相关测试文件。

## T11：支持上下文管理器从恢复历史初始化

**文件：** `src/okcode/context/manager.py`、`tests/unit/test_context_manager.py`  
**依赖：** T2

**步骤：**
1. 新增恢复初始化入口，从历史中重建全部用户原文记录。
2. 清除进程特有摘要、Token 锚点、失败计数和熔断状态。
3. 保持现有压缩规划和普通会话 API 行为不变。

**验证：** 运行 `uv run pytest tests/unit/test_context_manager.py -q`。

## T12：增加会话恢复可观察状态

**文件：** `src/okcode/models.py`、`tests/unit/test_conversation.py`  
**依赖：** T2

**步骤：**
1. 增加恢复失败或恢复无有效消息时的停止原因及安全提示文本。
2. 复用现有 `AgentProgress` 呈现恢复、截断和压缩进度。
3. 确认新增状态不会影响现有 Provider 流事件联合类型。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q`。

## T13：接入会话提交、存档和记忆任务

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T4、T9、T10、T12

**步骤：**
1. 扩展构造参数，注入日志、Worker 和运行时上下文工厂，保留默认依赖以兼容现有测试。
2. 正常请求使用运行时上下文；最终回答自然结束后依次更新内存历史、追加 JSONL、投递本轮记忆任务。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q`，覆盖新会话不回载旧消息、成功提交后存档和异步任务投递。

## T14：接入会话恢复、压缩和时间提醒

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T5、T11、T12、T13

**步骤：**
1. 添加会话列表查询和恢复协程，恢复只有在完整成功时才替换当前历史与活动日志。
2. 用恢复历史初始化 `ContextManager`，当恢复后超预算时调用已有摘要路径至多一次。
3. 根据最后活动时间添加一次性动态系统提醒，且不写入普通消息历史。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q`，覆盖选择前历史不变、恢复、压缩和时间提醒。

## T15：实现终端会话列表选择

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`、`tests/fakes.py`  
**依赖：** T2

**步骤：**
1. 用 Rich 表格展示会话 ID、标题、消息数和最近更新时间。
2. 实现编号选择、无会话提示和取消输入，错误编号留在选择循环而不改变会话。
3. 扩展终端替身，保持原有流式渲染测试可用。

**验证：** 运行 `uv run pytest tests/unit/test_terminal.py -q`。

## T16：在应用层编排 `/resume`

**文件：** `src/okcode/app.py`、`tests/unit/test_app.py`  
**依赖：** T14、T15

**步骤：**
1. 在主 REPL 精确拦截 `/resume`，从会话对象获取摘要并请求终端选择。
2. 用户取消、列表为空或选择非法时保持当前新会话不变。
3. 用户选中时运行恢复协程、渲染事件并以既有异常路径处理失败。

**验证：** 运行 `uv run pytest tests/unit/test_app.py -q`。

## T17：装配启动和关闭生命周期

**文件：** `src/okcode/cli.py`、`tests/unit/test_cli.py`  
**依赖：** T1、T4、T5、T7、T9、T10、T13、T16

**步骤：**
1. 在工作区初始化时清理过期会话、创建惰性日志、加载指令和创建记忆 Store。
2. 用当前活动 `ProviderConfig` 的工厂创建 `MemoryWorker`，并把所有依赖注入会话与应用。
3. 在 `finally` 中先关闭 Worker，再关闭 MCP、前台 Provider 和主 Runner；启动失败路径不得泄露后台线程。

**验证：** 运行 `uv run pytest tests/unit/test_cli.py -q`。

## T18：补充使用说明

**文件：** `README.md`  
**依赖：** T16、T17

**步骤：**
1. 更新当前会话仅内存的旧说明，解释新会话与长期记忆的区别。
2. 增加三层 `AGENTS.md`、`@include` 安全限制、`/resume`、存档期限和笔记目录说明。
3. 明确本阶段不包含向量检索、RAG、团队同步和自动恢复旧会话。

**验证：** 人工检查命令表与实际终端行为一致。

## T19：执行新增模块的聚焦验证

**文件：** 新增和修改的单元测试  
**依赖：** T1-T18

**步骤：**
1. 运行指令、会话、记忆、提示词、上下文、会话、终端、应用和 CLI 相关测试。
2. 修复新增模块间的导入、类型和异常边界问题。
3. 复跑所有聚焦测试直到全部通过。

**验证：** `uv run pytest tests/unit/test_instructions.py tests/unit/test_sessions.py tests/unit/test_memory_store.py tests/unit/test_memory_request.py tests/unit/test_memory_worker.py tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_app.py tests/unit/test_cli.py tests/unit/test_context_manager.py -q`。

## T20：执行全量回归与静态检查

**文件：** 全项目  
**依赖：** T19

**步骤：**
1. 运行全部测试，确认 Provider、工具、权限、MCP 和上下文管理行为未回归。
2. 运行格式检查和 Ruff 静态检查。
3. 复核 JSONL、笔记和索引临时测试文件未进入仓库。

**验证：** `uv run pytest -q`、`uv run ruff format --check .`、`uv run ruff check .`。

## 执行顺序

```text
T1
T2 -> T3 -> T4 -> T5
T6 -> T7 -> T8 -> T9
T1 + T7 -> T10
T2 -> T11 -> T12
T4 + T9 + T10 + T12 -> T13 -> T14
T2 -> T15
T14 + T15 -> T16
T1 + T4 + T5 + T7 + T9 + T10 + T13 + T16 -> T17 -> T18
T1-T18 -> T19 -> T20
```
