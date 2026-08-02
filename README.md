# OkCode

OkCode 是一个使用 Python 实现的终端 AI 编程助手。它在当前工作目录内协助阅读代码、定位文件、修改文本、执行命令、运行验证，并在模型需要工具时继续执行 Agent Loop，直到得到正式回答或触发安全上限。

每次启动都会创建新的短期会话，不会自动载入旧对话；但模型请求会加载项目指令、长期记忆索引、当前模式、可见工具和必要环境信息。已完成的短期会话写入项目内 JSONL 存档，只有输入 `/resume` 并选择会话后才恢复旧消息。

## 已实现能力

- 支持 OpenAI 兼容协议和 Anthropic Messages 协议，可通过 YAML 切换 Provider、模型、思考流和提示缓存。
- 支持流式回答、思考内容展示、Token 用量展示，以及 Provider 返回缓存字段时的缓存读取/写入展示。
- 内置 Agent Loop：同一轮中可处理多个工具调用，只读工具可并行执行，有副作用工具按顺序执行；每轮用户请求最多允许 12 次模型自主工具循环。
- 内置 6 项绑定工作区的本地工具：`read_file`、`write_file`、`edit_file`、`find_files`、`search_code`、`run_command`。
- 支持 MCP Server 工具发现；远端工具以 `mcp__<server>__<tool>` 暴露，并进入统一工具执行、权限和结果回灌链路。
- 支持 Skill 系统：启动时只暴露 Skill 名称和描述，模型按需调用 `load_skill` 读取完整 SOP；有效 Skill 会注册成动态斜杠命令。
- 支持子 Agent：可用预定义角色或 fork 当前对话快照；后台任务可用 `/tasks` 查看、后台化或取消。
- 支持子 Agent worktree 隔离：为任务创建受管理 Git worktree 和独立分支，本地文件/命令工具会绑定到隔离工作区。
- 支持 Hooks：可在消息、会话、工具和上下文压缩事件上注入 prompt、执行 shell、发送 HTTP 或启动子 Agent。
- 支持计划与执行模式：`/plan <任务>` 只开放只读工具并保存计划；`/do` 执行当前会话最近一次已保存计划。
- 内置五层本地工具权限控制：Windows 高危命令黑名单、项目路径沙箱、分层 YAML 规则、会话权限模式和默认确认。
- 底部状态栏显示当前运行模式和权限模式，例如 `[模式:DEFAULT] [权限:ALLOW]`。

## 安装

前置要求：Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --all-groups
```

## Provider 配置

在启动目录创建 `config.yaml`。该文件含 API Key，已被 `.gitignore` 忽略，不应提交。

```yaml
active: openai-compatible
providers:
  - name: openai-compatible
    protocol: openai
    model: your-model-name
    base_url: https://your-openai-compatible-endpoint/v1
    api_key: your-api-key
    thinking: false
    prompt_cache: false

  - name: claude
    protocol: anthropic
    model: your-claude-model
    base_url: https://api.anthropic.com
    api_key: your-api-key
    thinking: false
    prompt_cache: false
```

`active` 必须对应某个 Provider 的 `name`；`protocol` 仅支持 `openai` 和 `anthropic`。`thinking` 默认为 `false`，开启后会向对应协议请求其支持的思考流。

`prompt_cache` 默认为 `false`。仅当所选 Provider 明确支持提示缓存时才应开启：OkCode 会把稳定系统提示和工具声明作为缓存前缀，把工作区、日期、可用工具和任务模式等动态信息放入系统级补充消息。缓存是否实际命中以 Provider 返回的 usage 字段为准。

## MCP Server 配置

MCP Server 与 Provider 配置分开管理。OkCode 会按以下顺序读取 `mcp_servers`，项目级同名 Server 会完整覆盖用户级配置：

| 来源 | 路径 |
| --- | --- |
| 用户级 | `%USERPROFILE%/.okcode/config.yaml` |
| 项目级 | `<项目>/.okcode/config.yaml` |

两份文件都可以缺失。每个配置文件只包含 `mcp_servers`：

```yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: uvx
    args: ["@modelcontextprotocol/server-filesystem", "${WORKSPACE_ROOT}"]
    env:
      API_TOKEN: "${FILESYSTEM_TOKEN}"

  remote_search:
    transport: streamable_http
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer ${SEARCH_TOKEN}"
```

`args`、`env`、`url` 和 `headers` 的值支持 `${变量名}` 展开；引用未定义变量会以配置错误结束启动，且不会输出变量值。stdio 子进程会继承当前系统环境，配置中的 `env` 仅覆盖同名变量。

发现到的工具名称为 `mcp__<server>__<tool>`，避免与内置工具或其他 Server 冲突。远端工具默认按有副作用处理，因此继续经过权限规则和默认确认。单个 Server 无法连接、握手或发现失败时会显示启动告警，但不会阻止内置工具和其他 MCP Server 使用；当前版本不会自动重连。

## 运行

在希望让 OkCode 操作的项目根目录运行：

```powershell
uv run okcode
```

也可以使用模块入口：

```powershell
uv run python -m okcode
```

当前工作目录会成为主工作区根目录。文件、搜索和命令工具默认不能访问该目录之外的路径；worktree 子 Agent 会使用自己的隔离工作区根目录。

## 交互命令

| 输入 | 行为 |
| --- | --- |
| 普通自然语言任务 | 模型可使用当前模式下可见工具完成阅读、修改和验证。 |
| `/help` | 显示可用命令；包含动态 Skill 命令。 |
| `/status` | 显示权限模式、Token 用量、工具数量、记忆数量、模型名和工作目录。 |
| `/plan <任务>` | 切换到计划模式，只允许读取、查找和搜索工具；成功后保存计划。 |
| `/do` | 切换回默认模式并执行当前会话最近一次已保存计划。 |
| `/review` | 发起固定代码审查请求；不会读取 git diff 后再生成 prompt。 |
| `/compact` | 手动压缩当前上下文。 |
| `/clear` | 结束当前会话并开启新会话。 |
| `/resume` | 显示当前项目可恢复会话并按编号选择；回车或 `/cancel` 取消。 |
| `/session` | 显示当前会话标识和日志路径。 |
| `/memory` | 列出已加载的项目级和用户级记忆文件。 |
| `/permission` | 显示当前权限模式及用户、项目、本地规则文件路径。 |
| `/permission strict` | 未命中规则时直接拒绝。 |
| `/permission default` | 未命中规则时请求确认，默认模式。 |
| `/permission allow` | 未命中规则时直接放行；黑名单、路径沙箱和 `deny` 规则仍会拒绝。 |
| `/permissions ...` | `/permission ...` 的兼容别名。 |
| `/skill` | 列出可加载和已激活的 Skill。 |
| `/hooks` | 列出已加载 Hook 规则、最近状态和配置路径。 |
| `/tasks` | 列出后台子 Agent 任务。 |
| `/tasks background <task_id>` | 将前台任务标记为后台展示。 |
| `/tasks cancel <task_id>` | 取消后台子 Agent 任务。 |
| `/exit` 或 EOF | 退出 OkCode。 |

输入阶段按 `Ctrl+C` 会清空当前输入；生成阶段按 `Ctrl+C` 会取消本轮，已取消内容不会写入后续会话历史。

## 会话、指令与长期记忆

启动时会创建惰性会话日志，首轮正式回答成功后才在 `<项目>/sessions/` 下创建 `<YYYYMMDD-HHMMSS-xxxx>.jsonl`。每条消息单独追加为一行 JSON，列表中的标题、消息数和最近更新时间均直接扫描日志得到，不维护额外 meta 文件。超过 30 天的日志会在启动和查询时清理。

输入 `/resume` 后，终端会显示当前项目的可恢复会话表。输入编号恢复该会话；按回车或输入 `/cancel` 保持当前新会话不变。恢复会跳过损坏行，并在工具调用历史不完整时只保留最后一个合法消息边界。相隔较久的会话会在续聊的下一次模型请求前收到一次状态核对提醒。

手写项目指令按以下优先级合并，高优先级文本位于前面：

| 优先级 | 路径 |
| --- | --- |
| 高 | `<项目>/AGENTS.md` |
| 中 | `<项目>/.okcode/AGENTS.md` |
| 低 | `%USERPROFILE%/.okcode/AGENTS.md` |

指令文件可以使用单独一行的 `@include relative/path.md` 引用项目内文件。引用深度受限，循环、绝对路径、包含 `..` 的路径以及解析后跳出项目目录的符号链接均会被拒绝。

每轮 Agent 自然结束后，OkCode 在后台使用当前 Provider 更新长期记忆，不会阻塞终端继续输入。记忆统一存于项目内 `<项目>/.okcode/memory/`，其中项目级笔记位于 `project/`，用户级笔记位于 `user/`；笔记分为用户偏好、纠正反馈、项目知识和参考资料。两份索引会在普通请求前注入上下文，并各自限制为 200 行和 25KB。

## 权限规则

OkCode 在每次本地工具实际执行前检查权限。检查顺序固定为：Windows 高危命令黑名单、项目路径沙箱、会话/本地/项目/用户规则、权限模式、人工确认。前两层和命中的 `deny` 规则都是终局拒绝，不能被 `allow` 模式、规则或人工确认放开。

文件和目录工具会先解析符号链接或 Windows 重解析点，再确认目标仍位于当前工具绑定的工作区内。主 Agent 绑定主工作区；worktree 子 Agent 绑定隔离 worktree。`run_command` 仍由当前 Windows shell 执行，因此它使用黑名单、规则、模式和人工确认做准入控制，并不提供操作系统级命令沙箱。

规则文件按以下优先级查找，越靠前越优先：

| 来源 | 路径 | 是否建议提交 |
| --- | --- | --- |
| 当前会话 | 内存规则 | 否，退出 OkCode 后失效 |
| 项目本地 | `<项目>/.okcode/permissions.local.yaml` | 否，默认被忽略 |
| 项目共享 | `<项目>/.okcode/permissions.yaml` | 可以提交 |
| 用户全局 | `%USERPROFILE%/.okcode/permissions.yaml` | 仅本机使用 |

规则文件使用 YAML，按声明顺序匹配，首条命中规则生效：

```yaml
rules:
  - match: "Bash(git *)"
    action: allow
  - match: "write_file(.env)"
    action: deny
  - match: "write_file(regex:.*\\.secret$)"
    action: deny
```

规则写成 `工具名(模式)` 或只写 `工具名`。`Bash(...)` 是 `run_command(...)` 的兼容别名；模式支持旧版裸精确/裸 glob，也支持显式 `exact:`、`glob:`、`regex:` 和 `not:...` 反向匹配。命令模式匹配完整命令文本；路径模式匹配解析后的工作区相对路径，并统一使用 `/`。

在 `default` 模式下，未命中规则的调用会显示工具与命令或工作区相对路径。输入 `d` 拒绝、`o` 仅允许本次、`s` 允许本会话、`p` 永久允许。永久允许只会写入项目本地的 `permissions.local.yaml`；空输入、EOF、中断或无法识别的选择都会安全拒绝。`/exit` 会退出 OkCode，而不是被当成拒绝。

## Skill

Skill 用于把可复用 SOP 暴露给模型，但启动时只注入名称和描述；模型必须调用 `load_skill` 才能看到完整 SOP。已激活 Skill 使用激活时快照，直到 `/clear` 或重新加载该 Skill 才切换版本。

Skill 根目录按来源优先级加载，项目级覆盖用户级，用户级覆盖内置：

| 来源 | 路径 |
| --- | --- |
| 内置 | `src/okcode/skills/builtin/` |
| 用户级 | `%USERPROFILE%/.okcode/skills/` |
| 项目级 | `<项目>/.okcode/skills/` |

每个有效 Skill 会成为动态斜杠命令，例如 `/commit`、`/test`。动态命令不会直接注入 SOP，而是转发一条要求模型先调用 `load_skill` 的请求。静态内置命令优先级更高；例如外部 Skill 不能覆盖内置 `/review`。

## Hooks

Hooks 配置文件固定为 `<项目>/.okcode/hooks.yaml`；缺失文件等价于没有 Hook。支持的事件包括：

- `message.user`
- `session.start`
- `session.end`
- `tool.before`
- `tool.after`
- `system.context_compacted`

Hook 条件字段按事件白名单校验，匹配语法与权限规则一致，支持 `exact:`、`glob:`、`regex:` 和 `not:...`。动作支持 `prompt`、`shell`、`http` 和 `subagent`。`tool.before` 可以使用 `intercept: true` 拦截工具调用；权限、参数校验和黑名单仍然先于 Hook 生效。

```yaml
hooks:
  - name: block-secret-write
    event: tool.before
    if:
      - field: tool.name
        match: exact:write_file
      - field: tool.arguments.path
        match: regex:.*\\.secret$
    action:
      type: shell
      command: exit 1
      intercept: true
      deny_message: 不允许写入 secret 文件。
    control:
      timeout_seconds: 5
```

`/hooks` 会列出当前已加载规则、事件、动作、启用状态、最近运行状态和配置路径。

## 子 Agent 与 Worktree 隔离

OkCode 内置三个子 Agent 角色：

| 角色 | 用途 |
| --- | --- |
| `general-purpose` | 通用代码库任务，可读写文件、搜索代码并运行命令。 |
| `code-reviewer` | 审查局部代码变更并返回风险点，只开放读/查/搜工具。 |
| `researcher` | 搜索代码库并整理局部事实，只开放读/查/搜工具。 |

自定义角色按以下路径加载，项目级优先于用户级，用户级优先于内置：

| 来源 | 路径 |
| --- | --- |
| 内置 | `src/okcode/agents/builtin_roles/` |
| 用户级 | `%USERPROFILE%/.okcode/agents/` |
| 项目级 | `<项目>/.okcode/agents/` |

角色 frontmatter 支持 `tools.allow`、`tools.deny`、`model`、`max_turns`、`permission` 和 `isolation`。`permission` 支持 `inherit`、`default`、`strict`、`allow`；`isolation` 支持 `shared` 和 `worktree`。

模型可通过 `agent` 工具启动子 Agent：

- `kind=defined`：使用预定义角色；默认可前台运行，也可 `background=true`。
- `kind=fork`：继承父对话快照，强制后台运行。
- `isolation=shared`：沿用主工作区；后台任务默认只允许只读工具。
- `isolation=worktree`：从主仓库 HEAD 创建受管理 Git worktree；文件/搜索/命令工具绑定到隔离路径，后台任务可以在隔离路径内使用写入和命令工具。

worktree 默认位于 `<项目>/.okcode/worktrees/`，创建时会复制允许的本地配置、尝试链接大型依赖目录，并写入 `.okcode/worktree.json` 元数据。任务结束时，无变更的 worktree 会自动删除；存在未提交修改、未跟踪文件、未推送提交或状态不可确认时会保留并在任务结果中报告路径、分支、保留原因和变更摘要。

注意：Git worktree 只包含 Git 已跟踪的仓库状态，不会自动复制主工作区未提交或未跟踪文件。需要让 worktree 子 Agent 修改的文件应先加入 Git 历史。

## 开发验证

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```

常用定向验证：

```powershell
uv run pytest tests/unit/test_agents_*.py -q
uv run pytest tests/integration/test_subagent_worktree.py -q
uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q
```

## 当前范围

当前版本仍不支持 MCP 资源、提示词、采样、根目录协商、OAuth 专用认证、健康检查和自动重连；图片、音频与嵌入资源等非文本工具结果也不会交给模型处理。向量检索、RAG、团队记忆同步和自动化效果评估仍在后续范围。
