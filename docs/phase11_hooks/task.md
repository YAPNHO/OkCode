# OkCode 第十一阶段：Hooks 自动化机制 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | pyproject.toml、uv.lock | 将 httpx 作为运行时依赖锁定 |
| 新建 | src/okcode/matching.py | 权限和 Hook 共用的匹配表达式 |
| 新建 | src/okcode/hooks/__init__.py | Hook 模块公共导出 |
| 新建 | src/okcode/hooks/models.py | Hook 事件、条件、动作、控制和上下文值对象 |
| 新建 | src/okcode/hooks/config.py | .okcode/hooks.yaml 路径、严格加载和校验 |
| 新建 | src/okcode/hooks/actions.py | shell、prompt、HTTP、SubAgent 占位动作 |
| 新建 | src/okcode/hooks/runtime.py | 分发、once、提示词队列、后台任务与日志隔离 |
| 修改 | src/okcode/permissions/models.py | 使用共享 MatchExpression |
| 修改 | src/okcode/permissions/rules.py | 加载和写回扩展后的规则匹配文本 |
| 修改 | src/okcode/permissions/manager.py | 为 Hook shell 动作提供复用现有防线的授权入口 |
| 修改 | src/okcode/tools/executor.py | 插入 tool.before 与 tool.after Hook |
| 修改 | src/okcode/conversation.py | 轮次、消息、摘要事件和动态提示词接入 |
| 修改 | src/okcode/models.py | Hook 列表终端事件 |
| 修改 | src/okcode/commands/models.py | 会话端口增加 Hook 列表能力 |
| 修改 | src/okcode/commands/defaults.py | 注册 /hooks |
| 修改 | src/okcode/commands/handlers.py | 返回 Hook 列表事件 |
| 修改 | src/okcode/terminal.py | 渲染 /hooks 表格和空状态 |
| 修改 | src/okcode/app.py | session.start、session.end、system.error |
| 修改 | src/okcode/cli.py | 启动装配和后台 Hook 清理 |
| 新建 | tests/unit/test_matching.py | 共享匹配语法单元测试 |
| 新建 | tests/unit/test_hooks_models.py | Hook 值对象和上下文构造测试 |
| 新建 | tests/unit/test_hooks_config.py | YAML 读取、字段和跨字段校验测试 |
| 新建 | tests/unit/test_hooks_actions.py | shell、HTTP、SubAgent 动作测试 |
| 新建 | tests/unit/test_hooks_runtime.py | 分发、once、异步、注入状态和关闭测试 |
| 新建 | tests/unit/test_hooks_commands.py | /hooks 命令与会话端口测试 |
| 修改 | tests/unit/test_permissions_rules.py | 权限规则兼容与扩展匹配测试 |
| 修改 | tests/unit/test_permissions_manager.py | Hook shell 授权路径测试 |
| 修改 | tests/unit/test_tools_executor.py | 工具前置拦截和后置触发测试 |
| 修改 | tests/unit/test_conversation.py | 生命周期、提示词和压缩事件测试 |
| 修改 | tests/unit/test_app.py | 会话和错误 Hook 测试 |
| 修改 | tests/unit/test_cli.py | CLI Hook 装配和关闭测试 |
| 修改 | tests/unit/test_commands_handlers.py | 默认命令和 /hooks 测试 |
| 修改 | tests/unit/test_terminal.py | Hook 列表可见文本和表格测试 |
| 新建 | tests/integration/test_hook_lifecycle.py | 配置加载、拦截回灌和 Agent 继续运行的端到端测试 |

## T1：移动 HTTP 运行时依赖

**文件：** pyproject.toml、uv.lock

**依赖：** 无

**步骤：**

1. 将 httpx 从 dev 依赖组移动到项目 dependencies，保留当前最低版本约束。
2. 使用 uv 更新锁文件，确认只发生依赖组归属变更，没有无关版本漂移。
3. 在没有 dev 依赖组的运行环境中验证 httpx 可以被 Hook HTTP 动作导入。

**验证：** 运行 uv run python -c "import httpx; print(httpx.__version__)"，期望正常输出版本；运行 git diff -- uv.lock，期望变更只与 httpx 的运行时可用性相关。

## T2：实现共享匹配表达式

**文件：** src/okcode/matching.py、tests/unit/test_matching.py

**依赖：** T1

**步骤：**

1. 定义精确、glob、regex 三种正向匹配类型及带 negated 标记的不可变 MatchExpression。
2. 实现匹配文本解析：裸文本兼容现有精确/glob 自动判断，明确前缀选择对应类型，not 只能包裹一次正向表达式。
3. 在加载阶段编译正则并拒绝空文本、未知前缀、非法正则和嵌套 not。
4. 为精确、glob、regex、反向、裸模式兼容和失败路径添加单元测试。

**验证：** 运行 uv run pytest tests/unit/test_matching.py -q，期望所有匹配和配置错误用例通过。

## T3：让权限规则复用共享匹配器

**文件：** src/okcode/permissions/models.py、src/okcode/permissions/rules.py、tests/unit/test_permissions_rules.py

**依赖：** T2

**步骤：**

1. 将 PermissionRule 的模式表示替换为共享 MatchExpression，并保留路径目标的反斜杠规范化和大小写无关行为。
2. 调整规则文本解析、YAML 加载和本地永久 allow 规则写回，使老配置继续按原语义工作。
3. 增加权限规则的 regex、not、显式 glob 和裸模式回归用例。
4. 保证无效表达式仍携带 rules[index].match 的文件定位信息。

**验证：** 运行 uv run pytest tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py -q，期望现有权限优先级、allow/deny 和新增匹配语义均通过。

## T4：补充 Hook shell 的权限授权入口

**文件：** src/okcode/permissions/manager.py、tests/unit/test_permissions_manager.py

**依赖：** T3

**步骤：**

1. 抽取复用已有黑名单、工作区、规则和模式判断的内部授权路径，避免 Hook 重写或绕过权限逻辑。
2. 增加 Hook shell 调用的异步授权入口：同步 Hook 在 default 模式可沿用终端确认，后台 Hook 遇到待确认状态必须不弹提示并返回拒绝。
3. 确保 /exit 仍以 ExitRequested 冒泡到应用层，不会被转换成 Hook 失败或工具结果。
4. 覆盖黑名单、显式 deny、允许规则、default 前台确认、default 后台拒绝和 /exit 的测试。

**验证：** 运行 uv run pytest tests/unit/test_permissions_manager.py -q，期望 Hook 授权复用现有防线且不改变普通工具授权行为。

## T5：定义 Hook 领域模型和公共导出

**文件：** src/okcode/hooks/models.py、src/okcode/hooks/__init__.py、tests/unit/test_hooks_models.py

**依赖：** T2

**步骤：**

1. 定义十个生命周期事件、条件组合模式、提示词作用域、四种动作、执行控制、规则、上下文和拦截结果。
2. 把 action 声明为可区分联合类型，确保 shell 的 intercept 字段、prompt scope、HTTP 参数和 SubAgent task 都有明确类型。
3. 为 HookContext 提供只读、扁平的允许字段访问方式，不接受绝对路径、堆栈和任意对象。
4. 从 hooks 包导出配置路径、加载函数、运行时和必要模型，避免 CLI 依赖内部文件布局。

**验证：** 运行 uv run pytest tests/unit/test_hooks_models.py -q，期望事件、动作类型、上下文和包导入均可用。

## T6：实现 Hook 配置路径和基础 YAML 校验

**文件：** src/okcode/hooks/config.py、tests/unit/test_hooks_config.py

**依赖：** T5

**步骤：**

1. 实现 HookPaths.for_workspace，固定定位到工作区 .okcode/hooks.yaml。
2. 使用 yaml.safe_load 加载顶层 hooks 列表；文件不存在返回空规则集。
3. 校验顶层和规则的字段白名单、规则索引定位、可选 name 和顶层 enabled 默认值。
4. 覆盖缺失文件、合法空列表、非对象根节点、未知字段、错误 hooks 类型和错误 name/enabled 类型。

**验证：** 运行 uv run pytest tests/unit/test_hooks_config.py -q -k "path or root or basic"，期望缺失文件安全通过，所有错误包含文件路径和 hooks 索引。

## T7：实现条件、动作和控制的集中校验

**文件：** src/okcode/hooks/config.py、tests/unit/test_hooks_config.py

**依赖：** T2、T5、T6

**步骤：**

1. 解析 if 缺失、列表默认 all、all 和 any 对象；拒绝 all/any 混用、空条件列表、未知条件字段和错误 match 文本。
2. 根据事件校验受限字段路径，接受 tool.arguments. 后的参数名，拒绝不属于该事件的字段。
3. 严格解析四种 action 的必填字段和专属字段，校验 HTTP 方法、URL、headers、body 和 SubAgent task。
4. 解析 control 的 once、background 和 timeout_seconds；拒绝非正有限超时、错误 cwd、非 shell 的 intercept、非 tool.before 的 intercept，以及拦截规则后台执行。

**验证：** 运行 uv run pytest tests/unit/test_hooks_config.py -q，期望合法 YAML 转为完整 HookRule，所有跨字段非法组合在加载期失败。

## T8：实现 shell Hook 动作与拦截协议

**文件：** src/okcode/hooks/actions.py、tests/unit/test_hooks_actions.py

**依赖：** T4、T5、T7

**步骤：**

1. 定义可替换的 shell 进程启动端口，默认通过 asyncio 子进程在工作区或已校验 cwd 执行命令。
2. 将脱敏 HookContext 以 JSON 写入标准输入，收集退出码、标准输出、标准错误和超时结果，并限制日志输出大小。
3. 对普通 shell 动作，非零退出和超时只写日志结果；对 intercept 动作，非零退出或 stdout JSON 中 decision: deny 返回 HookInterception。
4. 拦截结果只使用 deny_message 或通用原因，禁止将命令输出和标准错误写回模型。

**验证：** 运行 uv run pytest tests/unit/test_hooks_actions.py -q -k "shell or intercept"，期望 JSON 输入、正常执行、非零、超时、JSON 拒绝和脱敏行为通过，且无需危险命令。

## T9：实现 HTTP、提示词和 SubAgent 占位动作

**文件：** src/okcode/hooks/actions.py、tests/unit/test_hooks_actions.py

**依赖：** T1、T5、T7

**步骤：**

1. 为 prompt 动作返回包含内容和作用域的执行结果，实际队列写入留给 HookRuntime。
2. 使用可注入的 httpx.AsyncClient 或 transport 执行 HTTP 动作，支持指定方法、URL、请求头、JSON 或文本 body 与超时。
3. 记录 HTTP 成功状态、非 2xx、传输异常和超时；不让这些错误冒泡到 HookRuntime 调用方。
4. 为 SubAgent 动作返回占位跳过结果，并记录规则名和 task 摘要，不创建任务、线程或 Provider 调用。

**验证：** 运行 uv run pytest tests/unit/test_hooks_actions.py -q -k "http or prompt or subagent"，期望使用 MockTransport 完成无外网 HTTP 验证，SubAgent 无真实副作用。

## T10：实现 HookRuntime 的匹配、顺序、once 和失败隔离

**文件：** src/okcode/hooks/runtime.py、tests/unit/test_hooks_runtime.py

**依赖：** T5、T8、T9

**步骤：**

1. 以 YAML 规则声明顺序筛选同一事件，计算无条件、all 和 any 条件的命中结果。
2. 对 enabled:false、未命中条件和已执行 once 的规则写入可测试日志，并确保 once 在首次安排动作前标记。
3. 顺序 await 前台动作；后台动作创建受追踪任务并安装异常回调。
4. 将动作异常、超时和日志记录错误全部吸收，只有合法 HookInterception 可以作为 dispatch 返回值。

**验证：** 运行 uv run pytest tests/unit/test_hooks_runtime.py -q -k "dispatch or condition or once or background"，期望规则顺序、跳过原因、异常隔离和唯一拦截结果正确。

## T11：实现提示词三层状态和后台任务清理

**文件：** src/okcode/hooks/runtime.py、tests/unit/test_hooks_runtime.py

**依赖：** T10

**步骤：**

1. 为 next_request、turn 和 session 三种 prompt scope 分别维护 SystemInstruction 状态。
2. 实现 system_instructions、mark_request_dispatched 和 end_turn，确保请求预构建不会消费 next_request，真实请求才消费，turn 结束仅清空当前轮次内容。
3. 实现 aclose，取消未完成后台任务、等待其收尾并记录取消或异常，不影响调用方退出。
4. 覆盖同一事件多个 prompt、连续请求、异常后台任务和关闭时仍在运行任务的测试。

**验证：** 运行 uv run pytest tests/unit/test_hooks_runtime.py -q -k "instruction or request or turn or close"，期望提示词不会污染 ChatMessage，后台任务可安全清理。

## T12：在 ToolExecutor 接入工具执行前 Hook

**文件：** src/okcode/tools/executor.py、tests/unit/test_tools_executor.py

**依赖：** T4、T10

**步骤：**

1. 为 ToolExecutor 增加可选 HookRuntime 依赖，默认 None 保持现有调用方和无 Hook 行为不变。
2. 在 JSON 和 Schema 校验、PermissionManager.authorize_async 成功后构造 tool.before 上下文并调用 dispatch。
3. 收到 HookInterception 时生成 ToolErrorCode.PERMISSION_DENIED 结果，加入 hook_rule、hook_event、executed:false 数据，不创建 PreparedToolCall。
4. 覆盖工具参数条件命中时业务工具执行计数为零、拒绝原因安全写入结果、Hook 不可用时原逻辑不变。

**验证：** 运行 uv run pytest tests/unit/test_tools_executor.py -q -k "hook_before or permission or rejection"，期望工具前置拒绝发生在真实业务执行之前。

## T13：在 ToolExecutor 接入工具执行后 Hook

**文件：** src/okcode/tools/executor.py、tests/unit/test_tools_executor.py

**依赖：** T10、T12

**步骤：**

1. 统一 execute_prepared 的成功、可预期失败、超时和内部错误返回路径，使每个已开始执行的调用都得到最终 ToolExecutionResult。
2. 在最终结果生成后构造 tool.after 上下文并调用 HookRuntime.dispatch，不允许后置 Hook 修改工具结果。
3. 确保后置 Hook 异常被 HookRuntime 吸收，既有输出截断、超时和结构化失败语义不变。
4. 覆盖成功、ToolFailure、超时和内部异常均触发一次后置事件；参数或权限预检失败不触发后置事件。

**验证：** 运行 uv run pytest tests/unit/test_tools_executor.py -q，期望原有执行器用例与新增后置事件用例全部通过。

## T14：在 ConversationSession 接入用户消息和轮次开始事件

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py

**依赖：** T4、T10

**步骤：**

1. 为 ConversationSession 增加可选 HookRuntime，默认 None 保持无 Hook 运行路径。
2. 在 stream_user_message 和 stream_do_instruction 构造用户消息后，按 message.user 再 turn.start 的顺序分发事件。
3. 事件上下文包含模式、轮次类型、轮次编号和消息文本，不包含内部 Provider 状态。
4. 覆盖普通消息、/do 派生消息和无 Hook 的既有行为。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "hook and (user or turn_start or do)"，期望事件顺序和上下文值正确。

## T15：在 ConversationSession 接入助手消息和轮次结束事件

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py

**依赖：** T14

**步骤：**

1. 在 Provider 完成事件已验证为助手消息后、工具分支判断前分发 message.assistant。
2. 为最终回答、工具循环停止、Provider 异常和正常工具循环整理轮次结果标识。
3. 用 finally 保证每个已开始的轮次只发出一次 turn.end，并调用 HookRuntime.end_turn 清理当轮注入。
4. 覆盖普通最终回答、工具调用后最终回答、异常回滚和迭代上限的结束事件。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "hook and (assistant or turn_end)"，期望完成事件只在消息合法后触发，轮次结束恰好一次。

## T16：在 Provider 请求中接入 Hook 提示词

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py

**依赖：** T11、T14

**步骤：**

1. 在 _build_normal_request 将 HookRuntime.system_instructions 追加到既有恢复提示、上下文摘要和 Skill 动态系统指令。
2. 保持 SystemInstruction 的优先级和现有 PromptBuilder 排序规则，不向 ChatMessage 历史插入伪造用户消息。
3. 在真正调用 provider.stream 前调用 mark_request_dispatched；自动压缩导致的预构建和重构建不得消费 next_request。
4. 覆盖 next_request、turn、session 三种作用域，以及工具调用和结果配对未被破坏的回归场景。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "hook and (prompt or instruction or request)"，期望 ProviderRequest 含正确注入而会话历史不新增伪消息。

## T17：在上下文压缩完成后接入系统事件

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py

**依赖：** T10、T16

**步骤：**

1. 在摘要提取、校验和 commit_summary 成功后分发 system.context_compacted。
2. 区分手动压缩和自动压缩，在上下文中记录压缩原因和摘要长度。
3. 保留现有摘要失败、熔断和历史不变行为；失败时不伪造“压缩成功”事件。
4. 覆盖自动压缩、手动压缩、摘要失败和 Hook 自身失败隔离。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "hook and compact"，期望仅成功提交摘要后发出事件。

## T18：增加 Hook 列表领域事件和终端渲染

**文件：** src/okcode/models.py、src/okcode/terminal.py、tests/unit/test_terminal.py

**依赖：** T5

**步骤：**

1. 定义 HookListEntry 和 HookListEvent，并把后者加入 TurnEvent 联合类型。
2. 让列表条目包含规则标识、事件、条件摘要、动作类型、enabled、once、background、超时和 SubAgent 占位状态。
3. 在 TerminalUI 中以 Rich 表格渲染 Hook 列表，保证较长条件和动作文本换行而不挤压表格。
4. 为无规则状态显示配置路径和明确中文提示，不输出内部配置原文。

**验证：** 运行 uv run pytest tests/unit/test_terminal.py -q -k hooks，期望 ANSI 渲染与去除样式后的可见文本都包含正确列表或空状态。

## T19：注册 /hooks 并暴露会话 Hook 列表

**文件：** src/okcode/commands/models.py、src/okcode/commands/defaults.py、src/okcode/commands/handlers.py、src/okcode/conversation.py、tests/unit/test_hooks_commands.py、tests/unit/test_commands_handlers.py

**依赖：** T10、T18

**步骤：**

1. 给 CommandConversationPort 增加返回 HookListEvent 的能力，并在 ConversationSession 从 HookRuntime 当前快照构造该事件。
2. HookRuntime.list_entries 将规则转换为展示模型，空配置同样提供配置路径。
3. 在默认命令注册表新增本地 /hooks，编写 hooks_command，不调用 Provider、不改变会话历史。
4. 覆盖命令帮助可见性、空状态、含 once/background/SubAgent 的条目与错误 Hook 配置不影响已加载快照。

**验证：** 运行 uv run pytest tests/unit/test_hooks_commands.py tests/unit/test_commands_handlers.py -q，期望 /hooks 只返回列表事件且不触发模型调用。

## T20：在应用层接入会话和错误 Hook

**文件：** src/okcode/app.py、tests/unit/test_app.py

**依赖：** T10、T18

**步骤：**

1. 为 OkCodeApp 注入可选 HookRuntime，在欢迎界面前分发 session.start。
2. 把现有主循环整理为 try/finally，确保 EOF、/exit、权限确认中的 ExitRequested 和运行时错误都会分发一次 session.end。
3. 在 ProviderError、KeyboardInterrupt 和一般运行时错误展示前生成脱敏 system.error 上下文；不得吞掉原有 UI 展示或退出行为。
4. 覆盖成功退出、权限 /exit、ProviderError、意外异常和 Hook 自身异常隔离。

**验证：** 运行 uv run pytest tests/unit/test_app.py -q -k "hook or exit or error"，期望会话事件完整，原有退出码和终端消息保持不变。

## T21：在 CLI 装配 HookRuntime 并清理后台任务

**文件：** src/okcode/cli.py、tests/unit/test_cli.py

**依赖：** T4、T6、T10、T12、T14、T20

**步骤：**

1. 在 Workspace、工具注册表和 PermissionManager 就绪后加载 Hook 配置，失败时走既有 ConfigError 启动提示。
2. 创建同一个 HookRuntime 并注入 ToolExecutor、ConversationSession 与 OkCodeApp，确保不会创建多个 once 状态容器。
3. 在 finally 中、关闭 Provider 前使用 Runner 调用 HookRuntime.aclose，记录而不掩盖关闭失败。
4. 覆盖缺失配置正常启动、无效配置退出码为 2、依赖注入一致性和后台任务关闭顺序。

**验证：** 运行 uv run pytest tests/unit/test_cli.py -q -k hook，期望启动和关闭路径可观察且不访问真实 Provider。

## T22：实现 Hook 生命周期端到端集成测试

**文件：** tests/integration/test_hook_lifecycle.py

**依赖：** T12、T13、T15、T16、T17、T19、T21

**步骤：**

1. 在临时工作区创建 hooks.yaml：一个 tool.before shell 守卫按参数阻止写入，一个 tool.after prompt 动作向下一请求注入内容。
2. 使用受控 Provider 先请求被拦截工具、再读取拦截结果并请求允许工具、最后输出正式回答。
3. 断言被拦截工具业务代码未执行、ToolExecutionResult 含可行动拒绝原因、模型第二次请求收到该结果并继续运行。
4. 断言 tool.after 的系统注入出现在随后的 ProviderRequest，且 assistant tool_calls 与 tool result 协议配对仍合法。

**验证：** 运行 uv run pytest tests/integration/test_hook_lifecycle.py -q，期望整个“配置加载 -> 拦截 -> 结果回灌 -> 调整调用 -> 注入下一请求 -> 最终回答”流程通过。

## T23：执行全量回归和静态检查

**文件：** 全部本阶段修改文件

**依赖：** T1 至 T22

**步骤：**

1. 运行新增 Hook 单元测试和受影响的权限、工具、会话、应用、命令、终端测试。
2. 运行完整 pytest 测试集，处理任何与无 Hook 行为、Provider 序列化、并行只读工具或权限确认有关的回归。
3. 运行 Ruff 格式检查、Ruff lint 和 Git 空白检查。
4. 用最小手工会话或受控 App 验证 /hooks 空状态与有规则状态的可见输出。

**验证：** 依次运行 uv run pytest -q、uv run ruff format --check .、uv run ruff check .、git diff --check，期望全部成功；/hooks 的两种状态均可在终端观察。

## 执行顺序

    T1
    -> T2
    -> T3 -> T4
    -> T5 -> T6 -> T7
    -> T8 -> T10 -> T11
    -> T9 -----^
    -> T12 -> T13
    -> T14 -> T15 -> T16 -> T17
    -> T18 -> T19
    -> T20
    -> T21
    -> T22
    -> T23

T4 可以在 T3 完成后与 T5 并行推进；T8 和 T9 可以在 T7 后并行推进；T18 可以在 T5 后与动作实现并行推进。实际执行时仍以每个任务的验证通过为完成条件。
