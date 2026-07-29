# OkCode 第五阶段：五层权限系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/okcode/permissions/__init__.py` | 导出权限模块公共接口 |
| 新建 | `src/okcode/permissions/models.py` | 权限领域模型、规则语法和匹配 |
| 新建 | `src/okcode/permissions/blacklist.py` | Windows 高危命令的不可绕过拦截 |
| 新建 | `src/okcode/permissions/rules.py` | YAML 加载、来源优先级和本地规则持久化 |
| 新建 | `src/okcode/permissions/manager.py` | 五层决策、模式、会话规则与确认处理 |
| 修改 | `src/okcode/tools/models.py` | 工具权限目标和权限拒绝错误类别 |
| 修改 | `src/okcode/tools/files.py` | 文件工具声明路径权限目标 |
| 修改 | `src/okcode/tools/search.py` | 搜索工具声明可选路径权限目标 |
| 修改 | `src/okcode/tools/command.py` | 命令工具声明命令权限目标 |
| 修改 | `src/okcode/tools/workspace.py` | 公共规范路径、相对路径和符号链接边界接口 |
| 修改 | `src/okcode/tools/executor.py` | 参数校验后的统一权限预检与执行边界 |
| 修改 | `src/okcode/conversation.py` | 顺序预检、拒绝回灌与 `/permissions` 命令 |
| 修改 | `src/okcode/models.py` | 权限状态事件 |
| 修改 | `src/okcode/terminal.py` | 终端确认、状态显示和拒绝来源摘要 |
| 修改 | `src/okcode/cli.py` | 规则加载与权限组件装配 |
| 修改 | `.gitignore` | 允许共享规则提交、忽略本地永久规则 |
| 新建 | `tests/unit/test_permissions_blacklist.py` | 黑名单的终局拒绝测试 |
| 新建 | `tests/unit/test_permissions_rules.py` | YAML、glob、别名、层级优先级与持久化测试 |
| 新建 | `tests/unit/test_permissions_manager.py` | 五层决策、模式和确认范围测试 |
| 修改 | `tests/unit/test_tools_workspace.py` | Windows 风格路径与重解析点逃逸测试 |
| 修改 | `tests/unit/test_tools_executor.py` | 权限预检发生在工具执行前的测试 |
| 修改 | `tests/unit/test_conversation.py` | 权限拒绝回灌、预检顺序和命令测试 |
| 修改 | `tests/unit/test_terminal.py` | 确认交互和权限状态渲染测试 |
| 修改 | `tests/unit/test_cli.py` | 权限 YAML 启动错误与正式装配测试 |

## T1：定义权限领域模型与规则语法

**文件：** `src/okcode/permissions/models.py`、`src/okcode/permissions/__init__.py`、`src/okcode/tools/models.py`

**依赖：** 无

**步骤：**

1. 定义 `PermissionMode`、`RuleAction`、`RuleSource`、`PermissionTargetKind` 和确认选择枚举，所有枚举值与设计文档一致。
2. 定义不可变的 `PermissionTarget`、`PermissionRequest`、`PermissionRule`、`RuleSet` 与 `PermissionDecision`。
3. 实现 `工具名` 与 `工具名(模式)` 语法解析，拒绝空文本、未闭合括号、嵌套括号、未知工具名和非字符串输入；将 `Bash` 规范化为 `run_command`。
4. 实现目标匹配：无模式只匹配工具名；命令匹配原始命令文本；路径匹配大小写无关的项目相对 POSIX 路径；精确与 glob 统一通过同一匹配函数处理。
5. 为 `ToolDefinition` 增加带安全默认值的 `permission_target` 字段；为权限拒绝增加稳定的 `ToolErrorCode.PERMISSION_DENIED`。
6. 在模块公开入口仅导出正式装配所需的类型，避免 CLI、会话和工具层从内部文件散乱导入。

**验证：** 新增最小单元测试，执行 `uv run pytest tests/unit/test_permissions_rules.py -q`，验证 `Bash(git *)` 别名、精确匹配、glob 匹配、路径大小写与格式归一、非法规则文本都符合预期。

## T2：实现 Windows 高危命令黑名单

**文件：** `src/okcode/permissions/blacklist.py`、`tests/unit/test_permissions_blacklist.py`

**依赖：** T1

**步骤：**

1. 定义仅处理 `run_command` 权限请求的预编译、大小写无关正则集合，并为每个高危类别保留不暴露绕过细节的中文说明。
2. 覆盖递归删除盘符根目录或系统范围目录、格式化卷、磁盘/分区清空或删除、启动配置破坏、强制关机和重启等 Windows 命令样本。
3. 返回 `RuleSource.BLACKLIST` 的终局 `PermissionDecision`；普通 `git status`、项目内 Python 命令等非黑名单样本返回未命中。
4. 不在此模块执行命令、读取规则或处理用户确认，保证黑名单完全独立且不可配置。

**验证：** 执行 `uv run pytest tests/unit/test_permissions_blacklist.py -q`；对每类高危样本断言拒绝，对正常命令断言不命中，测试不创建真实子进程。

## T3：强化工作区路径规范化与沙箱接口

**文件：** `src/okcode/tools/workspace.py`、`tests/unit/test_tools_workspace.py`

**依赖：** T1

**步骤：**

1. 抽取公共路径入口，将原始相对路径解析为已解析符号链接/重解析点后的工作区内路径，并能返回稳定的项目相对 POSIX 路径。
2. 对不存在的写入目标，解析所有已存在祖先后检查边界，防止父目录本身是项目外符号链接。
3. 继续拒绝空路径、`..` 回退、绝对路径、UNC 路径、盘符根目录和解析后工作区外的路径。
4. 保持现有 `resolve_path`、`resolve_directory`、`ensure_candidate` 和 `relative_path` 行为兼容，文件工具仍在自身内部重复保留路径边界防线。

**验证：** 执行 `uv run pytest tests/unit/test_tools_workspace.py -q`；除已有路径用例外，新增反斜杠相对路径、盘符大小写、UNC/绝对路径拒绝及外部符号链接或目录联接拒绝用例。平台无法创建链接时明确跳过该单项，而不是弱化其他断言。

## T4：实现三层 YAML 规则加载与项目本地持久化

**文件：** `src/okcode/permissions/rules.py`、`tests/unit/test_permissions_rules.py`、`.gitignore`

**依赖：** T1、T3

**步骤：**

1. 使用 `yaml.safe_load` 加载用户全局、项目级和项目本地规则文件；缺失文件返回空规则集。
2. 严格验证根节点、`rules` 列表、每条规则的字段集合、`match` 字符串、`action` 值和规则文本，并在错误中包含文件路径与规则索引。
3. 实现来源优先级解析：会话由管理器提供，持久层按项目本地、项目级、用户全局顺序返回首个命中；同一来源按声明顺序首个命中生效。
4. 实现永久允许的原子写入：创建 `<项目>/.okcode`，读取现有本地 YAML，追加等价 `allow` 规则，并以 UTF-8 保留已有规则顺序。写入失败向调用者报告而不伪造成功。
5. 调整 `.gitignore`，重新包含 `.okcode/permissions.yaml`，继续忽略 `.okcode/permissions.local.yaml` 和其他 `.okcode` 运行时内容。

**验证：** 执行 `uv run pytest tests/unit/test_permissions_rules.py -q`，验证缺失文件、格式错误、别名、精确/glob、单层首条规则、三层回退、项目本地覆盖、持久化写入和忽略规则。

## T5：实现五层权限管理器与确认范围

**文件：** `src/okcode/permissions/manager.py`、`tests/unit/test_permissions_manager.py`

**依赖：** T1、T2、T3、T4

**步骤：**

1. 构造 `PermissionManager`，接收工作区、已加载规则、已注册工具名、默认模式和可替换的确认回调。
2. 从已校验参数构造权限请求：命令使用完整文本；路径通过 `Workspace` 规范化为项目相对路径；可选目录参数缺失时使用 `.`。
3. 按黑名单、路径沙箱、会话规则、项目本地规则、项目规则、用户规则、模式、用户确认的顺序执行。黑名单、沙箱和任何 `deny` 命中必须立即终局返回。
4. 实现 `strict`、`default`、`allow` 未命中规则的兜底；`default` 调用确认回调，`allow` 和命中规则不调用确认。
5. 处理确认选择：本次允许只影响返回决定；本会话允许添加内存规则；永久允许先写入项目本地 YAML、成功后允许当前调用；拒绝、EOF、空输入、异常和未知选择全部拒绝。
6. 在拒绝决定中生成不含外部绝对路径或黑名单表达式的结构化数据，包括 `permission_source`、稳定拒绝类别、`executed=false` 和可行动建议。
7. 提供 `set_mode` 与 `status`，使调用方可读取默认模式、当前模式和三层规则文件位置。

**验证：** 执行 `uv run pytest tests/unit/test_permissions_manager.py -q`，覆盖固定安全顺序、`allow` 不能覆盖黑名单/沙箱/`deny`、三档模式、四种确认结果、会话规则复用、永久写入失败与安全拒绝。

## T6：给默认工具声明权限目标

**文件：** `src/okcode/tools/files.py`、`src/okcode/tools/search.py`、`src/okcode/tools/command.py`、`tests/unit/test_tools_executor.py`

**依赖：** T1、T3

**步骤：**

1. 为 `read_file`、`write_file`、`edit_file` 声明必填 `path` 目标。
2. 为 `find_files`、`search_code` 声明可选 `path` 目标；未提供时由权限层使用工作区根目录。
3. 为 `run_command` 声明必填 `command` 目标。
4. 复查工具 JSON Schema 与权限目标参数名称一致，避免工具通过未声明参数绕过匹配。

**验证：** 执行 `uv run pytest tests/unit/test_tools_files.py tests/unit/test_tools_search.py tests/unit/test_tools_command.py -q`，并增加元信息断言，确认六项默认工具暴露预期权限目标且既有工具行为不变。

## T7：在执行器接入统一权限预检

**文件：** `src/okcode/tools/executor.py`、`tests/unit/test_tools_executor.py`

**依赖：** T5、T6

**步骤：**

1. 将执行器的“查找工具、JSON 解析、Schema 校验”抽成可复用的已验证调用准备步骤。
2. 注入 `PermissionManager` 后，提供预检入口，返回可执行令牌或已封装的拒绝结果；拒绝时不进入 `Tool.execute`。
3. 提供仅接受已通过预检对象的执行入口，复用原有超时、`ToolFailure`、内部异常和输出截断逻辑。
4. 保持 `execute(call)` 兼容入口：正式装配有权限管理器时强制预检；没有管理器的旧单元测试沿用原执行语义，避免把无关工具测试改成终端交互测试。
5. 确保黑名单、路径沙箱、规则、模式和确认产生的拒绝都统一为 `ToolExecutionResult`，包含来源和 `executed=false`。

**验证：** 执行 `uv run pytest tests/unit/test_tools_executor.py -q`；用计数型测试工具断言允许调用执行一次，黑名单/规则/严格模式/确认拒绝均执行零次，且现有 JSON、Schema、超时和截断测试保持通过。

## T8：改造会话调度以串行预检、并发执行和回灌拒绝

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`

**依赖：** T7

**步骤：**

1. 将现有工具批次处理拆为“模型原始顺序的预检列表”和“允许调用的执行列表”，每个预检拒绝立即产生对应结果。
2. 只读批次在所有确认完成后，仅对允许调用并发执行；副作用工具继续逐个预检并串行执行。
3. 将预检拒绝与实际执行结果按模型原始工具调用索引排序，构造单个工具结果消息。
4. 不把 `permission_denied` 计为未知工具；保持其他停止条件与原子提交语义不变。
5. 使用可控 Provider 加入“首次调用被拒绝、第二轮选择替代工具并最终回答”的回归场景，断言工具失败结果出现在第二轮请求历史中，最终成功后才提交完整本轮。
6. 验证多只读调用在确认完成后仍保留并发能力，且拒绝其中一个调用不会阻止其他已允许调用按既有规则执行。

**验证：** 执行 `uv run pytest tests/unit/test_conversation.py -q`，重点检查拒绝回灌、调用排序、替代调用继续执行、并发只读和串行副作用工具。

## T9：添加权限状态事件和终端确认交互

**文件：** `src/okcode/models.py`、`src/okcode/terminal.py`、`tests/unit/test_terminal.py`

**依赖：** T5、T8

**步骤：**

1. 新增权限状态事件，包含当前模式、默认模式和三层 YAML 文件位置；扩展 `TurnEvent`。
2. 在 `TerminalUI` 增加同步确认方法，展示工具名、项目相对路径或命令、提示风险和四个中文选项。
3. 将明确输入映射为拒绝、本次、本会话、永久；`KeyboardInterrupt`、`EOFError`、空输入或其他文本一律映射为拒绝。
4. 更新工具完成摘要：权限拒绝时以简短中文展示“未执行”和决定来源，不暴露外部绝对路径或黑名单正则。
5. 欢迎信息添加当前权限模式；保证原有思考括号、回答分隔、工具状态和 Token 输出顺序不被破坏。

**验证：** 执行 `uv run pytest tests/unit/test_terminal.py -q`；使用替身 `PromptSession` 或 `Console` 覆盖四种选择、异常输入、状态展示和拒绝摘要，并保留既有 ANSI 样式与可见文本断言。

## T10：接入 `/permissions` 命令和 CLI 正式装配

**文件：** `src/okcode/conversation.py`、`src/okcode/cli.py`、`tests/unit/test_conversation.py`、`tests/unit/test_cli.py`

**依赖：** T4、T5、T7、T9

**步骤：**

1. 在 `ConversationSession.stream_turn()` 识别 `/permissions` 与 `/permissions strict|default|allow`，返回权限状态事件，不调用 Provider，不修改历史。
2. 对未知 `/permissions` 参数返回可理解的状态或停止事件，并保持当前模式不变。
3. CLI 在 `Workspace` 创建后收集注册表工具名、加载三层 YAML、构造 `PermissionManager`，注入 `TerminalUI` 确认回调，再传递给正式 `ToolExecutor` 与会话。
4. 将权限 YAML 的加载或校验错误转换为现有 `ConfigError` 路径，确保不创建 Provider 或启动 App。
5. 调整 CLI 与应用构造的类型依赖，使已有 Provider 生命周期、异常处理和 `config.yaml` 契约不变。

**验证：** 执行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_cli.py -q`，断言权限命令不访问 Provider、不污染历史，模式切换生效；YAML 错误返回退出码 2 且不创建 Provider；正常启动装配包含权限管理器。

## T11：执行局部到全量自动化验证并修复回归

**文件：** 仅修复在前述范围内发现的问题

**依赖：** T1 至 T10

**步骤：**

1. 先按模块执行权限、工作区、执行器、会话、终端和 CLI 的针对性测试，定位失败时仅修改对应实现或测试替身。
2. 执行完整测试集，检查 Provider 序列化、Plan Mode、工具行为与终端渲染没有被权限改造破坏。
3. 执行 Ruff 格式与静态检查，修复本阶段新增代码的格式、导入和类型问题。
4. 复查 Git 状态，确认新增 `.okcode/permissions.local.yaml` 没有被跟踪，且共享 `permissions.yaml` 规则路径可被跟踪。

**验证：** 依次运行：

```powershell
uv run pytest tests/unit/test_permissions_blacklist.py tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py -q
uv run pytest tests/unit/test_tools_workspace.py tests/unit/test_tools_executor.py tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_cli.py -q
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
git check-ignore -v .okcode/permissions.local.yaml
git check-ignore -v .okcode/permissions.yaml
```

预期：全部测试与 Ruff 检查通过；第一条 `git check-ignore` 显示本地规则被忽略，第二条对共享规则没有匹配的忽略规则并返回非零状态。

## 执行顺序

```text
T1
├-> T2
├-> T3
│   └-> T4
├-> T6
└-> T5 (依赖 T2、T3、T4)
    └-> T7 (依赖 T5、T6)
        └-> T8
            └-> T9
                └-> T10
                    └-> T11
```
