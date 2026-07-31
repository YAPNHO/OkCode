# OkCode 第十一阶段：Hooks 自动化机制 Plan

## 架构概览

本阶段新增独立的 hooks 模块，并将它接入 CLI 启动、应用会话生命周期、Agent 轮次、工具执行器、上下文压缩和斜杠命令。Hook 规则只从当前工作区的 .okcode/hooks.yaml 加载；缺失文件等价于没有 Hook。选择单一项目配置而不引入用户级和项目级多层覆盖，是因为需求未要求优先级或跨项目继承，避免把权限系统的复杂度复制到 Hook。

生命周期入口保持各自职责：

    CLI 启动
      -> Hook 配置加载、集中校验
      -> HookRuntime
      -> OkCodeApp：session.start / session.end / system.error
      -> ConversationSession：turn、message、context 事件和提示词读取
      -> ToolExecutor：tool.before / tool.after，真实执行前可拦截

HookRuntime 是事件分发、条件匹配、一次性状态、提示词注入状态、后台任务和日志隔离的唯一所有者。动作执行器只负责 shell、提示词、HTTP 和子 Agent 占位四类动作。Hook 配置、条件和值对象保持不可变；运行期的 once 标记、提示词队列和后台任务只存在于当前 OkCode 进程。

本阶段将权限规则当前的“精确或 glob”判断提升为共享匹配表达式。原有权限规则文本不加前缀时仍按原规则兼容：没有 glob 元字符就是精确匹配，有 glob 元字符就是 glob 匹配。新的前缀写法由权限规则和 Hook 条件共同使用：

    exact:文本
    glob:模式
    regex:正则
    not:exact:文本
    not:glob:模式
    not:regex:正则

not 只能包裹一次正向表达式，不能嵌套。regex 在加载时编译；glob 保留现有大小写和路径规范化语义。

## Hook YAML 契约

配置文件路径：

    <workspace>/.okcode/hooks.yaml

顶层只允许 hooks 列表。单条规则允许 name、enabled、event、if、action、control 字段；未声明 name 时使用 YAML 中从 1 开始的规则序号作为展示名和 once 标记键。

示例：

    hooks:
      - name: format-python-after-write
        event: tool.after
        if:
          all:
            - field: tool.name
              match: exact:write_file
            - field: tool.arguments.path
              match: glob:**/*.py
        action:
          type: shell
          command: uv run ruff format .
          cwd: .
        control:
          once: false
          background: true
          timeout_seconds: 15

      - name: protect-lockfile
        event: tool.before
        if:
          - field: tool.name
            match: exact:write_file
          - field: tool.arguments.path
            match: exact:uv.lock
        action:
          type: shell
          command: powershell -NoProfile -Command "exit 1"
          intercept: true
          deny_message: uv.lock 只能由依赖同步流程更新。
        control:
          timeout_seconds: 5

if 缺失时无条件命中。if 为列表时默认 all；if 为对象时只能包含 all 或 any 其中之一，值必须是非空条件列表。每个条件只能包含 field 和 match。field 采用受限点路径，不能执行任意表达式；match 使用共享匹配表达式。

事件和可读取字段固定如下：

| 事件 | 值 | 允许条件字段 |
|---|---|---|
| session.start | 会话创建完成 | session.id、runtime.mode |
| session.end | 会话准备退出 | session.id、session.turn_count |
| turn.start | 一次 Agent 轮次开始 | turn.kind、turn.index、runtime.mode、message.content |
| turn.end | 一次 Agent 轮次结束 | turn.kind、turn.index、turn.outcome |
| message.user | 用户消息已接收 | message.content、runtime.mode |
| message.assistant | Provider 完成助手消息 | message.content、message.tool_call_count、runtime.mode |
| tool.before | 参数校验和权限预检通过后、工具执行前 | tool.name、tool.safety、tool.target、tool.arguments.任意参数 |
| tool.after | 已执行工具生成最终结果后 | tool.name、tool.arguments.任意参数、tool.result.success、tool.result.error_code、tool.result.truncated |
| system.context_compacted | 摘要提交成功后 | context.reason、context.summary_length |
| system.error | 应用层捕获可记录错误后 | error.category、error.message |

字段白名单在加载期校验。tool.arguments. 后必须接一个参数名；运行时字段不存在时条件不命中。glob 和 regex 只能匹配字符串字段，exact 可以匹配字符串、数值和布尔值的稳定文本表示；不满足这些配置约束时加载失败。

action.type 只能是 shell、prompt、http、subagent：

| 类型 | 必填字段 | 可选字段 | 运行结果 |
|---|---|---|---|
| shell | command | cwd、intercept、deny_message | 通过 Windows shell 执行，记录输出、退出码和超时 |
| prompt | content | scope | 将文本写入下一请求、当前轮次或当前会话的系统指令 |
| http | url | method、headers、body | 发出 HTTP 请求并记录状态、错误或超时 |
| subagent | task | profile | 校验后只记录占位跳过，不启动子 Agent |

enabled 是规则顶层开关，默认 true。control 只包含 once、background、timeout_seconds，默认分别为 false、false、10。timeout_seconds 必须是有限正数。shell 的 cwd 缺失时使用工作区根目录，声明时只能是工作区内相对路径。intercept 只允许用于 tool.before 的 shell 动作，且该规则必须 background: false；违反时加载失败。

## 核心数据结构与接口

### 共享匹配器

新增 src/okcode/matching.py：

    class MatchKind(StrEnum):
        EXACT = "exact"
        GLOB = "glob"
        REGEX = "regex"

    @dataclass(frozen=True, slots=True)
    class MatchExpression:
        kind: MatchKind
        pattern: str
        negated: bool = False

        def matches(self, value: str) -> bool: ...

    def parse_match_expression(text: object, location: str) -> MatchExpression: ...

parse_match_expression 负责兼容裸模式、解析前缀、拒绝空文本和嵌套 not，并在加载期编译 regex。PermissionRule 改为保存 MatchExpression，而不是在每次匹配时猜测模式。PermissionRule.to_text 继续生成可写回 YAML 的稳定格式；原有永久允许规则仍生成兼容的裸精确文本。

### Hook 值对象

新增 src/okcode/hooks/models.py：

    class HookEvent(StrEnum):
        SESSION_START = "session.start"
        SESSION_END = "session.end"
        TURN_START = "turn.start"
        TURN_END = "turn.end"
        MESSAGE_USER = "message.user"
        MESSAGE_ASSISTANT = "message.assistant"
        TOOL_BEFORE = "tool.before"
        TOOL_AFTER = "tool.after"
        CONTEXT_COMPACTED = "system.context_compacted"
        ERROR = "system.error"

    class ConditionMode(StrEnum):
        ALL = "all"
        ANY = "any"

    class PromptScope(StrEnum):
        NEXT_REQUEST = "next_request"
        TURN = "turn"
        SESSION = "session"

    @dataclass(frozen=True, slots=True)
    class HookCondition:
        field: str
        expression: MatchExpression

    @dataclass(frozen=True, slots=True)
    class HookConditionGroup:
        mode: ConditionMode
        conditions: tuple[HookCondition, ...]

    @dataclass(frozen=True, slots=True)
    class HookControl:
        enabled: bool
        once: bool
        background: bool
        timeout_seconds: float

    @dataclass(frozen=True, slots=True)
    class ShellHookAction: ...

    @dataclass(frozen=True, slots=True)
    class PromptHookAction: ...

    @dataclass(frozen=True, slots=True)
    class HttpHookAction: ...

    @dataclass(frozen=True, slots=True)
    class SubAgentHookAction: ...

    type HookAction = ShellHookAction | PromptHookAction | HttpHookAction | SubAgentHookAction

    @dataclass(frozen=True, slots=True)
    class HookRule:
        identifier: str
        event: HookEvent
        conditions: HookConditionGroup | None
        action: HookAction
        control: HookControl

    @dataclass(frozen=True, slots=True)
    class HookContext:
        event: HookEvent
        values: Mapping[str, JSONValue]

    @dataclass(frozen=True, slots=True)
    class HookInterception:
        reason: str
        rule_identifier: str

HookContext 使用扁平、脱敏的允许字段映射，不保存绝对路径、异常堆栈或完整 ToolExecutionResult。条件解析器负责把点路径映射到这个只读上下文。

### 配置加载与 HookRuntime

新增 src/okcode/hooks/config.py：

    @dataclass(frozen=True, slots=True)
    class HookPaths:
        config: Path

        @classmethod
        def for_workspace(cls, workspace_root: Path) -> HookPaths: ...

    def load_hook_rules(paths: HookPaths) -> tuple[HookRule, ...]: ...

加载使用 yaml.safe_load 和严格字段白名单。文件不存在返回空元组；文件存在但 YAML、规则索引、字段、动作组合或条件语法无效时抛出 ConfigError，错误信息包含路径和 hooks[index] 定位。加载器还负责校验 event 对应字段、http 的 http/https URL、headers 的字符串映射、控制字段和子 Agent 占位字段。

新增 src/okcode/hooks/runtime.py：

    class HookRuntime:
        def __init__(
            self,
            rules: tuple[HookRule, ...],
            workspace: Workspace,
            permissions: PermissionManager,
        ) -> None: ...

        async def dispatch(self, context: HookContext) -> HookInterception | None: ...
        def system_instructions(self) -> tuple[SystemInstruction, ...]: ...
        def mark_request_dispatched(self) -> None: ...
        def end_turn(self) -> None: ...
        def list_entries(self) -> tuple[HookListEntry, ...]: ...
        async def aclose(self) -> None: ...

dispatch 按 YAML 声明顺序筛选相同 event 的规则。关闭规则和已执行的 once 规则被记录为跳过；once 在动作第一次被安排前写入内存集合，即使动作后续失败也不会重复运行。前台动作逐条 await；后台动作以 create_task 启动并由 done callback 统一记录异常，绝不把异常抛回 Agent。

prompt 动作不写入 ChatMessage。HookRuntime 维护三组 SystemInstruction：下一请求队列、当前轮次集合、当前会话集合。system_instructions 返回三者合并后的动态指令；mark_request_dispatched 只在 Provider 请求真正开始前消费下一请求队列，避免上下文压缩预检多次构建请求时丢失注入。end_turn 清空当前轮次注入，保留当前会话和未消费的下一请求注入。

新增 src/okcode/hooks/actions.py：

    class HookActionRunner:
        async def run(
            self,
            rule: HookRule,
            context: HookContext,
        ) -> HookInterception | None: ...

shell 动作使用 asyncio 子进程，JSON 格式的脱敏 HookContext 写入标准输入，捕获标准输出、标准错误、退出码，并以 HookControl.timeout_seconds 限时。普通 shell 非零退出只记日志。intercept: true 时，退出码非零或标准输出为单个 JSON 对象且 decision 等于 deny，都会产生 HookInterception；模型只能收到 deny_message 或通用拒绝原因，完整输出只进日志。

shell 动作会以 run_command 的命令和工作目录进入权限决策。同步 Hook 可使用既有权限确认；后台 Hook 不得发起终端确认，权限处于 default 待确认时按拒绝记录。危险命令黑名单、工作区路径边界和显式 deny 规则继续生效，Hook 不能绕过权限系统。

http 动作使用 httpx.AsyncClient。它支持方法、URL、请求头和 JSON 或文本请求体；非 2xx、传输异常和超时均写入日志。subagent 动作只记录带规则名和 task 摘要的“等待 SubAgent 阶段对接”日志。

## 生命周期接入与数据流

### 启动、关闭和错误事件

src/okcode/cli.py 在 Workspace、工具注册表和 PermissionManager 创建完成后加载 HookPaths，并创建 HookRuntime。HookRuntime 注入 ToolExecutor、ConversationSession 和 OkCodeApp。Hook 配置无效时沿用 ConfigError 启动失败路径，不创建半初始化 App。

OkCodeApp.run 在欢迎信息前同步消费 session.start；通过 try/finally 保证所有正常退出、/exit、EOF 和可恢复运行时异常都会消费 session.end。应用层现有 ProviderError、KeyboardInterrupt 和其他运行时异常在展示前调用 system.error，HookContext 只提供错误分类和可展示错误摘要，不传递堆栈或机密配置。

CLI finally 在关闭 Provider 前 await HookRuntime.aclose。aclose 取消仍在运行的后台 Hook 任务并记录取消，不因等待或取消失败改变 OkCode 的退出码。

### 轮次、消息和提示词

ConversationSession.stream_user_message 和 stream_do_instruction 在调用 _run_agent 前生成 message.user 与 turn.start，在 finally 中生成 turn.end。_run_agent 在 Provider 完成并校验 assistant 消息后、分支处理工具调用前生成 message.assistant。

ConversationSession._build_normal_request 将 HookRuntime.system_instructions 的返回值追加到既有动态 SystemInstruction。它仍保留上下文摘要、恢复提示、Skill 和模式提示的既有优先级。每次即将进入 provider.stream 前调用 mark_request_dispatched，确保 next_request 注入只在真实请求开始时消费。

上下文摘要成功提交后，ConversationSession._run_summary 生成 system.context_compacted，并写入压缩原因和摘要长度；失败路径维持已有停止行为，只让 HookRuntime 记录自身失败，不改变摘要熔断逻辑。

### 工具前置拦截与后置事件

ToolExecutor.prepare 的顺序调整为：

    查找工具
    -> JSON 解析和 Schema 校验
    -> PermissionManager.authorize_async
    -> HookRuntime.dispatch(tool.before)
    -> PreparedToolCall 或结构化失败结果

因此 Hook 条件总是拿到可信的结构化参数，且 Hook 永远不能绕过权限层。dispatch 返回 HookInterception 时，ToolExecutor 不调用工具实现，返回 ToolErrorCode.PERMISSION_DENIED 的 ToolExecutionResult；数据包含 hook_rule、hook_event 和 executed:false，内容使用 HookInterception 的安全原因加上“调用未执行，请调整参数或改用其他方案”。ConversationSession 已有的工具结果回灌逻辑会把该失败结果写入下一次模型请求。

ToolExecutor.execute_prepared 在工具成功、预期失败、超时和内部失败都已转换为 ToolExecutionResult 后，调用 HookRuntime.dispatch(tool.after)。后置 Hook 不修改结果；自身任何异常都被 HookRuntime 吸收。现有连续只读工具并行执行策略保持不变；每个工具调用内部的同事件 Hook 仍按 YAML 顺序执行。

## 命令和终端展示

新增 HookListEntry 与 HookListEvent 到 src/okcode/models.py，并加入 TurnEvent 联合类型。HookListEntry 包含展示名、事件、条件摘要、动作类型、enabled、once、background、timeout_seconds 和 subagent_placeholder。

ConversationSession 新增 hook_list_event 方法，将 HookRuntime.list_entries 转为 HookListEvent；未配置时事件带空条目和 Hook 配置路径。CommandConversationPort 增加对应方法。

src/okcode/commands/defaults.py 注册本地 /hooks 命令，src/okcode/commands/handlers.py 的 hooks_command 返回 HookListEvent。src/okcode/terminal.py 以 Rich 表格展示各规则字段；空列表明确显示“当前未加载 Hook 规则”和配置文件路径。/hooks 只读取当前已验证快照，不静默热重载配置。

## 文件组织

    src/okcode/
    ├── matching.py                     共享精确、glob、regex、反向匹配表达式
    ├── hooks/
    │   ├── __init__.py                 对外导出 HookPaths、HookRuntime、load_hook_rules
    │   ├── models.py                   事件、条件、动作、控制、上下文和值对象
    │   ├── config.py                   YAML 路径、严格加载与集中校验
    │   ├── runtime.py                  分发、once、提示词状态、后台任务和日志隔离
    │   └── actions.py                  shell、prompt、HTTP、sub Agent 占位动作
    ├── permissions/
    │   ├── models.py                   改用共享 MatchExpression
    │   └── rules.py                    保持兼容地加载和写回扩展匹配语法
    ├── tools/executor.py               权限之后的 tool.before 和 tool.after 接入
    ├── conversation.py                 轮次、消息、摘要、提示词与 /hooks 会话端口
    ├── app.py                          会话开始、结束和应用错误事件
    ├── cli.py                          Hook 启动装配与退出清理
    ├── commands/
    │   ├── defaults.py                 注册 /hooks
    │   ├── handlers.py                 hooks_command
    │   └── models.py                   会话端口新增 Hook 列表能力
    ├── models.py                       HookListEntry、HookListEvent、TurnEvent
    └── terminal.py                     /hooks Rich 表格渲染

    tests/
    ├── unit/
    │   ├── test_matching.py
    │   ├── test_hooks_config.py
    │   ├── test_hooks_runtime.py
    │   ├── test_hooks_actions.py
    │   ├── test_hooks_commands.py
    │   ├── test_permissions_rules.py
    │   ├── test_tools_executor.py
    │   ├── test_conversation.py
    │   ├── test_app.py
    │   └── test_terminal.py
    └── integration/
        └── test_hook_lifecycle.py

pyproject.toml 把 httpx 从仅开发依赖移动到运行时依赖；uv.lock 随依赖组变更更新。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Hook 配置作用域 | 单一工作区 .okcode/hooks.yaml | 用户只要求 YAML 声明，不要求多层覆盖；避免新增规则优先级和冲突语义。 |
| 匹配器 | 新增共享表达式并兼容权限裸模式 | Hook 和权限不会长期产生不同的精确、glob、regex、反向行为。 |
| 条件语法 | 受限字段路径 + all 或 any | 不使用 Python 表达式或 eval，保证加载期校验、安全性和可测试性。 |
| 工具拦截 | 同步 shell 守卫退出码或 JSON decision | 可读取结构化工具参数，能表达细粒度策略，并把安全原因返还模型。 |
| 拦截位置 | Schema 与权限之后、工具业务代码之前 | 参数可信，原有不可绕过权限仍优先，真实副作用尚未发生。 |
| 提示词注入 | SystemInstruction 运行时队列 | 不伪造用户消息，不破坏 Provider 的工具调用和工具结果相邻配对。 |
| 后台任务 | create_task 加追踪和退出取消 | 满足非阻塞需求，避免未处理异常和进程退出时悬挂任务。 |
| HTTP 客户端 | httpx.AsyncClient | 原生异步、超时明确、可用 MockTransport 做无外网测试；同时作为运行时依赖声明。 |
| Hook Shell 权限 | 复用 PermissionManager，后台不弹确认 | Hook 不绕过黑名单、沙箱和规则；后台任务不能竞争终端输入。 |
| /hooks 刷新策略 | 展示已验证快照，不自动热重载 | 符合本阶段不做文件监听的边界，避免执行中的规则瞬时替换。 |

## Spec 覆盖检查

| 需求 | 设计归属 |
|---|---|
| F1、F12 | HookPaths、load_hook_rules、严格 YAML 校验 |
| F2 | HookEvent、App、ConversationSession、ToolExecutor 生命周期接入 |
| F3 | ToolExecutor 在权限后、业务执行前处理 HookInterception |
| F4、F5 | matching.py、HookConditionGroup 和字段白名单 |
| F6、F10、F11 | HookActionRunner、HookControl、后台任务追踪和日志隔离 |
| F7 | HookRuntime 的 SystemInstruction 三层队列和 ConversationSession 请求提交点 |
| F8 | httpx AsyncClient 动作执行器 |
| F9 | SubAgentHookAction 的严格校验和占位日志 |
| F13 | HookListEntry、HookListEvent、/hooks 命令和终端表格 |
| N1、N2 | 无配置空运行、权限优先、脱敏上下文和既有结果回灌 |
| N3、N4、N5 | logging、集中校验、可替换 HTTP/进程执行器与单元/集成测试 |
