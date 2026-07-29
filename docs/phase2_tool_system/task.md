# OkCode 第二阶段：工具系统 Tasks

> 本任务拆解以已批准的 `spec.md` 和 `plan.md` 为唯一实现基线。开发开始前必须先批准本文件和 `checklist.md`；所有命令以仓库根目录为工作目录。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`uv.lock` | 添加并锁定 JSON Schema 参数校验依赖。 |
| 修改 | `src/okcode/models.py` | 扩展角色、工具调用、会话消息和工具显示事件。 |
| 新建 | `src/okcode/tools/__init__.py`、`models.py`、`base.py`、`registry.py`、`executor.py`、`workspace.py`、`defaults.py` | 工具基础契约、注册、执行、工作区边界和默认装配。 |
| 新建 | `src/okcode/tools/files.py`、`search.py`、`command.py` | 六项核心工具实现。 |
| 修改 | `src/okcode/providers/base.py` | 将工具定义加入统一 Provider 请求接口。 |
| 修改 | `src/okcode/providers/openai.py` | OpenAI 工具声明、流式片段拼接和工具历史序列化。 |
| 修改 | `src/okcode/providers/anthropic.py` | Anthropic 工具声明、`input_json` 拼接和工具历史序列化。 |
| 修改 | `src/okcode/conversation.py` | 单工具执行、三消息原子提交和本轮停止。 |
| 修改 | `src/okcode/app.py`、`terminal.py`、`cli.py` | 回合事件渲染、工具状态摘要与默认工具装配。 |
| 修改 | `tests/fakes.py`、`tests/unit/test_models.py`、`test_conversation.py`、`test_terminal.py`、`test_app.py`、`test_cli.py` | 统一模型、会话、终端、应用和启动装配测试。 |
| 新建 | `tests/unit/test_tools_registry.py`、`test_tools_executor.py`、`test_tools_workspace.py`、`test_tools_files.py`、`test_tools_search.py`、`test_tools_command.py` | 工具层独立单元测试。 |
| 修改 | `tests/integration/test_openai_sse.py`、`test_anthropic_sse.py` | 两种协议的工具 SSE 和历史回灌测试。 |
| 新建 | `tests/integration/test_tool_turn.py` | 受控工作区的单工具回合端到端测试。 |

## T1：添加 Schema 校验依赖

**文件：** `pyproject.toml`、`uv.lock`  
**依赖：** 无

**步骤：**

1. 在运行依赖中添加与 Python 3.12 兼容的 `jsonschema`。
2. 保持已有运行和开发依赖不变，重新解析并锁定依赖。

**验证：** 运行 `uv lock && uv sync --all-groups && uv run python -c "import jsonschema; print(jsonschema.__version__)"`，预期成功完成且打印已安装版本。

## T2：定义工具领域对象与扩展会话消息

**文件：** `src/okcode/tools/__init__.py`、`src/okcode/tools/models.py`、`src/okcode/models.py`、`tests/unit/test_models.py`  
**依赖：** T1

**步骤：**

1. 在 `tools/models.py` 定义 JSON 可序列化类型、工具定义、工具输出、错误码和结构化执行结果，并提供稳定 JSON 序列化入口。
2. 在 `models.py` 增加 `Role.TOOL`、`ToolCall`，并使 `ChatMessage` 可携带调用、结果和 Provider 私有状态。
3. 增加 `ToolExecutionStarted`、`ToolExecutionFinished` 与回合 UI 事件联合类型；保留既有文本与思考流事件。
4. 为普通文本消息、工具调用消息和工具结果消息建立不变量校验，拒绝冲突字段或缺失必需字段。
5. 添加测试覆盖消息不变量、错误结果 JSON 序列化和 `provider_state` 不出现在表示文本中。

**验证：** 运行 `uv run pytest tests/unit/test_models.py -q`，预期所有领域对象构造、约束和序列化测试通过。

## T3：实现工具接口与注册中心

**文件：** `src/okcode/tools/base.py`、`src/okcode/tools/registry.py`、`tests/unit/test_tools_registry.py`  
**依赖：** T2

**步骤：**

1. 定义异步 `Tool` Protocol，只暴露 `definition` 和 `execute(arguments)`。
2. 实现 `ToolRegistry.register()`、`get()` 与稳定排序的 `definitions()`。
3. 对空名称、重复名称及查询不存在工具提供明确的领域错误或可检测返回值。
4. 用最小假工具测试登记、按名查询、重复拒绝、稳定顺序和完整元信息传递。

**验证：** 运行 `uv run pytest tests/unit/test_tools_registry.py -q`，预期登记和拒绝场景均通过。

## T4：实现统一执行器的 JSON、Schema 与结果治理

**文件：** `src/okcode/tools/executor.py`、`tests/unit/test_tools_executor.py`  
**依赖：** T1、T2、T3

**步骤：**

1. 实现执行入口：按调用名查询工具，解析 `arguments_json`，用该工具的 JSON Schema 校验参数，再调用工具。
2. 将未知工具、无效 JSON、Schema 失败、`asyncio.TimeoutError` 和未预期异常转换为 `ToolExecutionResult`，不得传播给会话层。
3. 对返回给模型的 `content` 和 JSON 数据采用统一大小限制；截断时设置 `truncated=true` 并保留可理解提示。
4. 使用可控假工具验证成功、未知工具、JSON 错误、未知字段、超时、内部异常和输出截断。

**验证：** 运行 `uv run pytest tests/unit/test_tools_executor.py -q`，预期每个失败情形均返回结构化结果且测试进程不报未处理异常。

## T5：实现工作区路径边界

**文件：** `src/okcode/tools/workspace.py`、`tests/unit/test_tools_workspace.py`  
**依赖：** T2

**步骤：**

1. 在构造时解析并保存工作区根目录，提供工作区相对路径和受保护绝对路径之间的转换。
2. 拒绝绝对路径、空路径、父级回退段、非目录搜索根及解析后不在根目录内的目标。
3. 对已存在目标与创建新文件目标分别验证最终路径；解析符号链接后离开工作区时返回 `outside_workspace`。
4. 在临时目录中测试合法路径、`..`、绝对路径、工作区外目标和可用平台上的越界符号链接。

**验证：** 运行 `uv run pytest tests/unit/test_tools_workspace.py -q`，预期所有越界案例均被拒绝且合法嵌套路径保持可用。

## T6：实现文件读取工具

**文件：** `src/okcode/tools/files.py`、`tests/unit/test_tools_files.py`  
**依赖：** T4、T5

**步骤：**

1. 定义 `read_file` 的名称、描述和禁止额外字段的 JSON Schema。
2. 通过 `Workspace` 解析路径，以 UTF-8 有界读取常规文本文件。
3. 将不存在、目录目标、解码失败和 IO 异常转换为可诊断工具输出；大文件只返回受限前缀并让执行结果标记截断。
4. 测试正常读取、找不到文件、目录、无效编码、越界路径与大文件截断。

**验证：** 运行 `uv run pytest tests/unit/test_tools_files.py -q -k "read"`，预期覆盖的读取与失败场景通过。

## T7：实现原子写文件工具

**文件：** `src/okcode/tools/files.py`、`tests/unit/test_tools_files.py`  
**依赖：** T5、T6

**步骤：**

1. 定义 `write_file` 的 `path`、`content` Schema，并复用工作区边界验证。
2. 创建缺失父目录，在目标同目录写入临时文件后以原子替换提交。
3. 成功结果返回相对路径、字符数和新建父目录数；写入异常时清理临时文件并返回 IO 失败结果。
4. 测试新文件、覆盖文件、嵌套父目录、越界路径和模拟替换失败后的原文件完整性。

**验证：** 运行 `uv run pytest tests/unit/test_tools_files.py -q -k "write"`，预期内容、摘要和失败时的文件完整性都正确。

## T8：实现唯一原文替换工具

**文件：** `src/okcode/tools/files.py`、`tests/unit/test_tools_files.py`  
**依赖：** T5、T7

**步骤：**

1. 定义 `edit_file` 的 `path`、`old_text`、`new_text` Schema。
2. 在写入前完整统计原文本出现次数：零次返回 `match_not_found`，多次返回 `match_not_unique`，且两种情况不创建或替换任何文件。
3. 唯一命中时生成替换内容，并复用同目录临时文件和原子替换路径。
4. 测试成功替换、零次匹配、多次匹配、包含换行文本、越界路径和写入失败回滚。

**验证：** 运行 `uv run pytest tests/unit/test_tools_files.py -q -k "edit"`，预期失败分支验证原文件字节内容完全未变。

## T9：实现模式找文件与代码搜索工具

**文件：** `src/okcode/tools/search.py`、`tests/unit/test_tools_search.py`  
**依赖：** T4、T5

**步骤：**

1. 定义 `find_files` 的 `pattern` 和可选 `path` Schema，按工作区相对路径稳定排序返回匹配文件。
2. 定义 `search_code` 的 `query`、可选 `path`、可选 `pattern` Schema，逐行搜索 UTF-8 文本并记录路径、行号和行内容。
3. 跳过无法解码的文件及解析后越界的候选符号链接；对起始目录越界返回结构化失败。
4. 对匹配文件数、命中数、单行长度和总结果施加限制，并验证截断标记。
5. 用临时工作区覆盖递归模式、相对路径、行号、模式过滤、无匹配、无效编码、越界和结果截断。

**验证：** 运行 `uv run pytest tests/unit/test_tools_search.py -q`，预期查找、搜索、安全和截断场景均通过。

## T10：实现命令执行与进程回收

**文件：** `src/okcode/tools/command.py`、`tests/unit/test_tools_command.py`  
**依赖：** T4

**步骤：**

1. 定义 `run_command` 的 `command` Schema，并以工作区根目录作为子进程 `cwd`。
2. 使用当前系统 shell 启动独立进程组，并并发有界读取标准输出和标准错误。
3. 正常退出时返回退出码和输出；非零退出返回 `command_failed`；超时后回收整个进程树或进程组并返回 `timeout`。
4. 通过当前 Python 解释器构造跨平台测试命令，覆盖工作目录、成功输出、非零退出、超时和超量输出截断；不运行破坏性命令。

**验证：** 运行 `uv run pytest tests/unit/test_tools_command.py -q`，预期超时进程被回收、非零退出保持可读输出且无测试残留子进程。

## T11：装配默认六工具

**文件：** `src/okcode/tools/defaults.py`、`src/okcode/tools/__init__.py`、`tests/unit/test_tools_registry.py`  
**依赖：** T3、T6、T7、T8、T9、T10

**步骤：**

1. 创建以 `Workspace` 为输入的默认注册表工厂。
2. 在确定顺序中登记 `read_file`、`write_file`、`edit_file`、`run_command`、`find_files`、`search_code`。
3. 测试默认注册表恰好包含六个唯一名称，所有工具均有说明、对象 Schema 和正超时值。

**验证：** 运行 `uv run pytest tests/unit/test_tools_registry.py -q -k "default"`，预期六个工具都可按名查询并可向 Provider 暴露定义。

## T12：扩展 Provider 接口和测试替身

**文件：** `src/okcode/providers/base.py`、`tests/fakes.py`、`tests/unit/test_conversation.py`  
**依赖：** T2、T11

**步骤：**

1. 将 `LLMProvider.stream()` 签名扩展为接收工具定义序列。
2. 扩展 `FakeProvider` 记录工具定义与请求历史，保留原有文本流、异常和关闭观测能力。
3. 为会话测试提供可控假执行器或真实轻量注册表，使后续会话测试可以断言一次工具执行与历史提交。
4. 更新现有调用点测试，确保普通文本回合仍向 Provider 提供六个工具定义而不改变文本结果。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q -k "successful_turn"`，预期现有文本回合保持通过且假 Provider 可观察工具定义。

## T13：实现 OpenAI 工具声明与流式调用拼接

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`  
**依赖：** T2、T11、T12

**步骤：**

1. 将内部工具定义转换为 Chat Completions `tools` 参数，并在工具可用时设置 `parallel_tool_calls=False`。
2. 扩展流解析器，以工具调用索引累积 `id`、函数 `name` 和 `arguments` JSON 分片。
3. 结束时将恰好一个完整调用构造成 assistant 工具调用消息；多个调用、字段缺失或流不完整时返回 Provider 流错误。
4. 保留既有 `reasoning_content` 和正式文本流行为，并测试文本与一次工具调用同时出现时的完成语义。
5. 使用假流和受控原始 SSE 流，将调用 ID、名称和 JSON 拆为多个增量，验证只产生一个完整调用。

**验证：** 运行 `uv run pytest tests/integration/test_openai_sse.py -q -k "tool or thinking or history"`，预期工具 Schema、禁用并行、分片拼接和原有 thinking 测试通过。

## T14：实现 OpenAI 工具历史序列化

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`  
**依赖：** T13

**步骤：**

1. 序列化普通消息、assistant 工具调用消息和 `role="tool"` 工具结果消息。
2. 将结果序列化 JSON 作为工具消息内容，并确保 `tool_call_id` 与原调用 ID 精确匹配。
3. 保持第一阶段 OpenAI 历史只回传正式回答、不回传 reasoning 私有状态的规则。
4. 通过第二次假请求断言历史包含合法、相邻的 assistant `tool_calls` 与 `role="tool"`，同时覆盖工具成功和工具失败。

**验证：** 运行 `uv run pytest tests/integration/test_openai_sse.py -q -k "tool_history"`，预期两种结果均生成符合协议的历史消息。

## T15：实现 Anthropic 工具声明与输入 JSON 拼接

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`  
**依赖：** T2、T11、T12

**步骤：**

1. 将内部工具定义映射为 Messages `tools` 参数，并设置 `tool_choice.type="auto"` 和 `disable_parallel_tool_use=true`。
2. 在流中识别 `tool_use` 内容块，并从 SDK `input_json` 事件累积 `partial_json`。
3. 使用最终内容块的 ID、名称和累积参数构造一次工具调用消息；多个工具块、缺失字段、无效 JSON 或停止原因不一致时返回 Provider 流错误。
4. 保持既有 extended thinking、text 事件和原始内容块保存逻辑兼容。
5. 使用假事件和受控 SSE 流分片验证一个完整调用的还原和并行限制请求参数。

**验证：** 运行 `uv run pytest tests/integration/test_anthropic_sse.py -q -k "tool or thinking"`，预期工具调用片段和既有 thinking 流测试通过。

## T16：实现 Anthropic 工具历史序列化

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`  
**依赖：** T15

**步骤：**

1. 对工具调用 assistant 消息保存并重放原始 `tool_use` 内容块，保留调用 ID、名称和输入。
2. 将内部工具结果消息转换为紧邻的 user `tool_result` 内容块；失败时设置 `is_error=true`。
3. 通过第二次假请求断言成功与失败结果均形成合法的 `[assistant tool_use, user tool_result]` 序列。
4. 覆盖具有 thinking 私有块的普通历史与工具历史混合时仍能正确序列化。

**验证：** 运行 `uv run pytest tests/integration/test_anthropic_sse.py -q -k "tool_history"`，预期成功和失败工具结果均可被下一次请求重放。

## T17：实现会话单工具执行与原子提交

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T4、T12、T13、T14、T15、T16

**步骤：**

1. 向会话注入 `ToolRegistry` 和 `ToolExecutor`，每次 Provider 请求均传入当前注册表定义。
2. 保持普通文本回合的“用户 + assistant”原子提交与取消/Provider 错误回滚行为。
3. 对一个完整工具调用依次发出开始、结束 UI 事件，执行一次工具，并原子提交“用户、assistant 调用、tool 结果”三条消息。
4. 工具执行成功或失败均提交成对历史并结束本轮；不得调用 Provider 第二次。
5. 多个调用、无完成事件、完成后增量或不合法 assistant 消息时回滚整轮；测试历史中不存在孤立调用或结果。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q`，预期普通回合、成功工具回合、失败工具回合、回滚和不自动二次请求都通过。

## T18：渲染工具状态并接入应用循环

**文件：** `src/okcode/terminal.py`、`src/okcode/app.py`、`tests/unit/test_terminal.py`、`tests/unit/test_app.py`  
**依赖：** T2、T17

**步骤：**

1. 将应用循环从仅渲染增量改为渲染统一回合事件。
2. 终端对工具开始事件显示工具名和执行中状态；对结束事件显示成功/失败和短摘要，不输出完整 JSON 或命令大输出。
3. 维持思考区、回答区、错误、中断和结束时的换行状态正确；工具回合结束后仍调用 `finish_turn()` 并回到提示符。
4. 测试成功工具、失败工具、文本回合和工具后 `/exit` 的终端输出与应用循环状态。

**验证：** 运行 `uv run pytest tests/unit/test_terminal.py tests/unit/test_app.py -q`，预期工具摘要和既有文本交互测试全部通过。

## T19：在 CLI 装配工作区与默认工具系统

**文件：** `src/okcode/cli.py`、`tests/unit/test_cli.py`  
**依赖：** T11、T17、T18

**步骤：**

1. 以启动时 `Path.cwd()` 创建 `Workspace`、默认工具注册表和 `ToolExecutor`，再创建会话。
2. 保持配置错误、Provider 创建失败和资源关闭的已有行为；工具初始化失败也走安全启动失败提示，不泄漏内部堆栈。
3. 用替身验证 CLI 将当前工作目录和六工具注册表传递到会话，不访问真实 API 或真实命令。

**验证：** 运行 `uv run pytest tests/unit/test_cli.py -q`，预期启动装配和既有退出资源管理测试通过。

## T20：完成端到端回合、全量回归与静态检查

**文件：** `tests/integration/test_tool_turn.py`、全部受影响测试文件  
**依赖：** T18、T19

**步骤：**

1. 在临时工作区创建受控文本文件，并让假 Provider 返回一次 `read_file` 工具调用。
2. 通过真实 `ConversationSession`、默认注册表和假终端执行一轮，断言读取结果进入 tool 消息、界面收到开始/完成事件、Provider 只被调用一次。
3. 以第二次 Provider 请求验证回灌历史分别符合 OpenAI 和 Anthropic 的序列化规则。
4. 运行全量测试与 Ruff；修正仅由本章改动引入的问题。

**验证：** 依次运行 `uv run pytest -q` 与 `uv run ruff check src tests`，预期均成功且端到端测试验证本章“单次工具调用后停止”的完整链路。

## 执行顺序

```text
T1 → T2 → T3 → T4
               ├→ T5 → T6 → T7 → T8
               ├→ T9
               └→ T10

T3 + T6 + T8 + T9 + T10 → T11 → T12
T2 + T11 + T12 → T13 → T14
T2 + T11 + T12 → T15 → T16
T4 + T12 + T14 + T16 → T17 → T18 → T19 → T20
```

其中 T5、T9、T10 在 T4 后可并行；T13/T14 与 T15/T16 也可并行，但实现期间应共享 `models.py` 的已确定契约，避免同时改动同一文件。
