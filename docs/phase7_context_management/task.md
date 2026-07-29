# OkCode 第七阶段：上下文管理 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/okcode/context/__init__.py` | 导出上下文管理公共类型 |
| 新建 | `src/okcode/context/models.py` | 配置、状态、锚点、外置元数据与摘要计划 |
| 新建 | `src/okcode/context/artifacts.py` | 会话隔离的原子工具结果外置 |
| 新建 | `src/okcode/context/manager.py` | 轻量压缩、预算估算、摘要候选与熔断 |
| 新建 | `src/okcode/context/summary.py` | 摘要提示、转录和正式摘要解析 |
| 修改 | `src/okcode/conversation.py` | 请求前压缩、`/compact`、摘要调用与原子提交 |
| 修改 | `src/okcode/models.py` | 上下文压缩失败与熔断停止原因 |
| 修改 | `src/okcode/prompt/builder.py` | 合并动态摘要和边界系统补充 |
| 修改 | `src/okcode/cli.py` | 使用工作区根目录装配上下文管理 |
| 新建 | `tests/unit/test_context_artifacts.py` | 外置写盘和边界测试 |
| 新建 | `tests/unit/test_context_manager.py` | 轻量选择、估算、保留尾部和熔断测试 |
| 新建 | `tests/unit/test_context_summary.py` | 摘要请求和输出解析测试 |
| 修改 | `tests/unit/test_conversation.py` | 自动、手动、失败与 Usage 接入测试 |
| 修改 | `tests/unit/test_prompt_builder.py` | 动态上下文补充测试 |
| 修改 | `tests/integration/test_tool_turn.py` | 真实工具轮次的外置接入测试 |

## T1：建立上下文领域模型

**文件：** `src/okcode/context/__init__.py`、`src/okcode/context/models.py`

**依赖：** 无

**步骤：**
1. 定义固定阈值的 `ContextConfig`。
2. 定义 `TokenEstimateAnchor`、`ConversationContextState`、`ToolResultArtifact` 和 `SummaryPlan`。
3. 在会话状态中保存逐字用户原文、摘要文本、边界文本、Usage 锚点和熔断状态。
4. 仅从包入口导出后续调用所需的公共类型。

**验证：** `uv run python -c "from okcode.context import ContextConfig; assert ContextConfig().automatic_compaction_tokens == 167000"` 通过。

## T2：实现会话隔离的工具结果外置

**文件：** `src/okcode/context/artifacts.py`

**依赖：** T1

**步骤：**
1. 以工作区根目录和会话标识构造 `.okcode/context/<会话标识>/tool-results/`。
2. 将完整稳定 JSON 结果写入临时文件并原子替换为最终文件。
3. 返回工作区相对路径、原始字符数和保留工具调用元数据的预览结果构造信息。
4. 保证文件名不直接使用未校验的工具名称或外部路径。

**验证：** `uv run pytest tests/unit/test_context_artifacts.py -q` 中的原子写盘用例通过。

## T3：覆盖外置边界和还原内容

**文件：** `tests/unit/test_context_artifacts.py`

**依赖：** T2

**步骤：**
1. 测试恰为 `50_000` 字符的结果不被外置。
2. 测试超过 `50_000` 字符的结果写入会话目录，返回路径为工作区相对路径。
3. 从落盘文件读取并断言内容等于原 `ToolExecutionResult.to_json()`。
4. 模拟写入失败，断言调用方可获得失败而没有被替换的历史结果。

**验证：** `uv run pytest tests/unit/test_context_artifacts.py -q` 全部通过。

## T4：实现轻量压缩和 Token 估算

**文件：** `src/okcode/context/manager.py`

**依赖：** T1、T2

**步骤：**
1. 按完整稳定 JSON 字符数实现单结果 `50K` 外置和单消息 `200K` 聚合外置。
2. 聚合超限时按原始大小降序选择，大小相等时维持原工具结果顺序。
3. 实现 Usage 锚点加字符增量的请求估算，以及 Usage 不可用时的全文字符估算。
4. 根据 `167K` 判断自动摘要需求，并维护正常请求 Usage 锚点。

**验证：** `uv run pytest tests/unit/test_context_manager.py -q -k "light or estimate"` 通过。

## T5：实现摘要候选、尾部保留和熔断状态

**文件：** `src/okcode/context/manager.py`

**依赖：** T4

**步骤：**
1. 从已完成历史中选择摘要前缀，保留尾部至不少于约 `10K` Token 或 5 条消息的较大者。
2. 将保留边界扩展到完整轮次，避免分离助手工具调用和工具结果。
3. 构建包含旧摘要、待摘要转录和原始用户消息的 `SummaryPlan`。
4. 实现成功提交、连续失败计数清零、三次失败熔断和熔断后拒绝摘要计划。

**验证：** `uv run pytest tests/unit/test_context_manager.py -q -k "plan or retain or circuit"` 通过。

## T6：补齐上下文管理器单元测试

**文件：** `tests/unit/test_context_manager.py`

**依赖：** T4、T5

**步骤：**
1. 使用 `42K、38K、45K、40K、44K` 结果断言仅外置 `45K`，其余四项保持原文。
2. 覆盖 Usage 锚点、全文回退估算和 `167K` 自动阈值。
3. 覆盖最少 5 条消息、约 10K Token 和完整轮次边界。
4. 覆盖成功重置失败计数、三次失败熔断和熔断后拒绝新计划。

**验证：** `uv run pytest tests/unit/test_context_manager.py -q` 全部通过。

## T7：实现摘要请求构造与正式摘要解析

**文件：** `src/okcode/context/summary.py`

**依赖：** T1

**步骤：**
1. 将旧摘要和待摘要消息转录为有来源标签的摘要输入。
2. 构造独立的 `ProviderRequest`：工具为空、缓存关闭、系统提示禁止工具调用并要求分析草稿和正式摘要分区。
3. 要求正式区域含九个固定标题及“所有用户消息”占位标记。
4. 解析时丢弃草稿，程序原样填充用户消息；拒绝缺失正式区域、标题或占位标记的响应。

**验证：** `uv run pytest tests/unit/test_context_summary.py -q` 中的请求构造与解析用例通过。

## T8：覆盖摘要契约与非法响应

**文件：** `tests/unit/test_context_summary.py`

**依赖：** T7

**步骤：**
1. 断言摘要请求没有工具定义、没有缓存，并包含禁止调用工具的指令。
2. 断言分析草稿不会出现在提取后的正式摘要。
3. 断言九段标题齐全，原始用户消息逐字替换到第六段。
4. 覆盖工具调用、空响应、缺失标题和缺失占位标记等失败分支。

**验证：** `uv run pytest tests/unit/test_context_summary.py -q` 全部通过。

## T9：向提示构建器注入摘要与边界消息

**文件：** `src/okcode/prompt/builder.py`、`tests/unit/test_prompt_builder.py`

**依赖：** T1

**步骤：**
1. 为 `PromptBuildContext` 增加默认空的额外系统补充输入。
2. 将上下文摘要和边界消息作为动态系统补充按优先级并入提示。
3. 确保它们不会改变稳定系统提示或缓存键。
4. 测试补充内容可见、顺序稳定且不进入缓存前缀。

**验证：** `uv run pytest tests/unit/test_prompt_builder.py -q` 全部通过。

## T10：装配上下文管理器与停止原因

**文件：** `src/okcode/models.py`、`src/okcode/cli.py`、`src/okcode/conversation.py`

**依赖：** T1、T2、T4、T5、T7、T9

**步骤：**
1. 增加上下文压缩失败和摘要熔断的停止原因，复用现有终端事件渲染。
2. 在 CLI 用当前 `Workspace` 根目录创建会话隔离的外置存储和上下文管理器。
3. 为 `ConversationSession` 注入可选上下文管理器，并在构造时保留现有调用方兼容性。
4. 在正常用户和计划任务进入会话时记录用户原文，供摘要第六段确定性使用。

**验证：** `uv run pytest tests/unit/test_cli.py tests/unit/test_models.py -q` 通过。

## T11：接入正常请求前的自动压缩

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`

**依赖：** T9、T10

**步骤：**
1. 在工具结果加入 `pending` 前调用轻量压缩。
2. 在每次正常 Provider 请求前构造完整上下文、注入系统补充并执行预算判断。
3. 自动摘要时仅消费内部 Provider 流，拒绝工具调用响应，成功后原子替换已完成历史并重建正常请求。
4. 正常请求完成后记录真实 Usage；摘要失败时停止当前轮而不发送正常 Provider 请求。

**验证：** `uv run pytest tests/unit/test_conversation.py -q -k "automatic or usage or tool"` 通过。

## T12：接入 `/compact`、失败和熔断路径

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`

**依赖：** T11

**步骤：**
1. 在现有斜杠命令分派中增加 `/compact`，低预算时仍强制请求摘要。
2. 没有可摘要历史时输出无操作进度，不调用 Provider。
3. 每次摘要失败保持原历史并递增失败计数；第三次失败后输出熔断停止原因。
4. 熔断后阻止自动和手动摘要 Provider 调用，成功摘要重置失败计数。

**验证：** `uv run pytest tests/unit/test_conversation.py -q -k "compact or circuit or summary"` 通过。

## T13：验证真实工具轮次与全量回归

**文件：** `tests/integration/test_tool_turn.py`

**依赖：** T3、T6、T8、T9、T11、T12

**步骤：**
1. 在真实工具执行链中制造大结果，断言下一次模型请求接收到预览和可回读路径，而不是原完整结果。
2. 保留既有工具调用、权限、计划和 Provider 集成用例，确认没有回归。
3. 运行全量单元与集成测试、格式检查和静态检查。

**验证：**
```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```
三条命令全部通过。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6
  \-> T7 -> T8
  \-> T9
T6 + T8 + T9 -> T10 -> T11 -> T12 -> T13
```
