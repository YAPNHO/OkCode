# OkCode

OkCode 是一个使用 Python 编写、运行在 Windows 终端中的轻量级 AI Coding Agent，其核心能力是在当前项目目录内完成代码读取、问题定位、文件修改、命令执行与结果验证，并通过 MCP、Skill、子 Agent 和长期团队协作机制扩展工程能力。项目借助 Codex 完成模块编码与调试，并以 Spec 工程范式驱动需求澄清、系统设计、技术方案制定和任务拆解。

OkCode 默认只访问当前工作区，所有本地文件和命令工具都会绑定到这个工作区。会话、记忆、团队和临时 worktree 都保存在本地，不会自动上传到远端。

## 目录

- [5 分钟开始](#5-分钟开始)
- [核心能力](#核心能力)
- [日常命令](#日常命令)
- [内置工具](#内置工具)
- [Provider 配置](#provider-配置)
- [权限控制](#权限控制)
- [会话与长期记忆](#会话与长期记忆)
- [MCP](#mcp)
- [Skill](#skill)
- [子 Agent 与 Worktree](#子-agent-与-worktree)
- [Hooks](#hooks)
- [Team Lead](#team-lead)
- [隐私与本地数据](#隐私与本地数据)
- [开发与验证](#开发与验证)
- [当前边界](#当前边界)

## 5 分钟开始

### 1. 准备环境

- Windows
- Python 3.12 或更高版本（小于 3.14）
- [uv](https://docs.astral.sh/uv/)
- 一个可用的 OpenAI 兼容或 Anthropic API

### 2. 安装依赖

在项目根目录执行：

```powershell
uv sync --all-groups
```

### 3. 创建 Provider 配置

在项目根目录创建 `config.yaml`。`api_key` 只保存在本机，不要提交到 Git：

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

  # 使用 Anthropic 时可保留这一项，并把 active 改为 claude
  - name: claude
    protocol: anthropic
    model: your-claude-model
    base_url: https://api.anthropic.com
    api_key: your-api-key
    thinking: false
    prompt_cache: false

team:
  coordinator_enabled: false
  terminal_backend_priority: [terminal_pane, coroutine]
```

配置要求：

- `active` 必须与某个 Provider 的 `name` 完全一致。
- `protocol` 只能是 `openai` 或 `anthropic`。
- `base_url` 必须是有效的 HTTP(S) 地址。
- `thinking` 和 `prompt_cache` 默认关闭，只有确认当前 Provider 支持时再开启。
- `team` 配置可以省略；团队默认保存到项目目录下的 `.okcode/team/`。

### 4. 启动

```powershell
uv run okcode
```

也可以使用模块入口：

```powershell
uv run python -m okcode
```

### 5. 尝试第一个任务

启动后直接用自然语言描述目标，例如：

```text
读取 src/okcode/conversation.py，解释它如何处理一轮工具调用，然后运行相关测试。
```

常见工作流是“先读代码，再修改，最后运行测试”。OkCode 会在模型需要时自动调用工具，并持续执行多轮 Agent Loop。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| Agent Loop | 在一轮请求中完成多次工具调用，支持流式文本、思考内容和 token 用量展示。 |
| 文件与命令 | 读取、创建、编辑文件，查找文件和代码，运行当前工作区内的 Windows 命令。 |
| 计划执行 | `/plan` 只读分析并保存计划，`/do` 执行最近一次计划。 |
| 权限系统 | 支持 `strict`、`default`、`allow` 三种模式，叠加黑名单、路径边界和 YAML 规则。 |
| 会话恢复 | 会话以 JSONL 保存在当前项目，使用 `/resume` 恢复。 |
| 长期记忆 | 在项目级和用户级记忆目录中维护可检索笔记。 |
| MCP | 发现并注册外部 MCP Server 工具，统一纳入权限和执行链路。 |
| Skill | 按需加载可复用 SOP，并生成动态斜杠命令。 |
| 子 Agent | 支持预定义角色、fork 快照、后台任务和 Worktree 隔离。 |
| Hooks | 在会话、轮次、消息、工具和错误事件上执行 prompt、shell、HTTP 或 subagent 动作。 |
| Team Lead | 创建长期团队，派生成员，分配任务，通过共享任务列表和邮箱协作。 |

## 日常命令

| 命令 | 用途 |
| --- | --- |
| `/help` | 查看内置命令和当前已注册的动态 Skill 命令。 |
| `/status` | 查看权限模式、token 用量、工具数量、记忆数量、模型和工作区。 |
| `/plan <任务>` | 进入计划模式，只开放读、查找和搜索工具，并保存计划。 |
| `/do` | 执行当前会话最近保存的计划。 |
| `/review` | 发起一次代码审查请求。 |
| `/compact` | 手动压缩当前上下文。 |
| `/clear` | 结束当前会话并开启新会话。 |
| `/resume` | 列出并恢复当前项目中的历史会话。 |
| `/session` | 显示当前会话标识和日志路径。 |
| `/memory` | 查看当前已加载的项目级和用户级记忆。 |
| `/permission` | 查看或修改权限模式。可用参数：`strict`、`default`、`allow`。 |
| `/hooks` | 查看已加载的 Hook 规则和最近状态。 |
| `/skill` | 查看可加载和已激活的 Skill。 |
| `/tasks` | 查看后台子 Agent；可使用 `cancel` 或 `background <task_id>` 控制任务。 |
| `/team` | 查看当前团队状态。配合 `create`、`use`、`leave`、`status` 管理团队。 |
| `/exit` | 退出 OkCode；EOF 也会执行正常退出清理。 |

输入阶段按 `Ctrl+C` 可清空当前输入；生成阶段按 `Ctrl+C` 可取消本轮，已取消内容不会写入后续会话历史。

## 内置工具

普通会话默认提供六个绑定到当前工作区的工具：

| 工具 | 用途 |
| --- | --- |
| `read_file` | 读取文本文件。 |
| `write_file` | 创建或覆盖文件。 |
| `edit_file` | 对文件执行精确编辑。 |
| `find_files` | 按名称或 glob 查找文件。 |
| `search_code` | 在代码和文本中搜索内容。 |
| `run_command` | 在当前工作区执行 Windows shell 命令。 |

`agent`、`load_skill`、MCP 工具和团队工具会根据当前上下文动态加入。所有工具都经过统一的参数校验、工作区边界检查、权限判断和 Hook 链路。

## Provider 配置

Provider 配置文件是当前启动目录下的 `config.yaml`。OkCode 支持：

- OpenAI Chat Completions 兼容协议；
- Anthropic Messages 协议；
- 模型名称、API 地址、思考模式和提示缓存开关；
- 运行时根据 `active` 选择一个 Provider。

当前版本不会替 Provider 自动重试或切换模型。Provider 配置错误会在启动阶段直接提示，并不会进入半初始化状态。

## 权限控制

权限检查发生在每一次本地工具真正执行之前，顺序固定为：

1. Windows 高风险命令黑名单；
2. 工作区路径沙箱；
3. 本地、项目、用户规则；
4. 当前权限模式；
5. 需要时的人工确认。

规则文件：

| 范围 | 路径 | 说明 |
| --- | --- | --- |
| 当前会话 | 内存中 | 退出 OkCode 后失效。 |
| 项目本地 | `<项目>/.okcode/permissions.local.yaml` | 默认忽略，不建议提交。 |
| 项目共享 | `<项目>/.okcode/permissions.yaml` | 可以随项目提交。 |
| 用户全局 | `%USERPROFILE%/.okcode/permissions.yaml` | 只在本机生效。 |

YAML 规则示例：

```yaml
rules:
  - match: "Bash(git *)"
    action: allow
  - match: "write_file(.env)"
    action: deny
  - match: "write_file(regex:.*\\.secret$)"
    action: deny
```

`deny` 规则和系统黑名单是最终拒绝，不能被 `allow` 或人工确认覆盖。使用 `/permission` 查看或切换权限模式。

## 会话与长期记忆

### 会话

成功提交过正式回复的会话会写入：

```text
<项目>/sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl
```

会话日志按行追加 JSON，启动和查询时会清理超过保留期的日志。使用 `/resume` 可以选择历史会话继续工作；损坏或不完整的尾部记录会被跳过。

### 指令文件

项目指令按以下优先级加载：

1. `<项目>/AGENTS.md`
2. `<项目>/.okcode/AGENTS.md`
3. `%USERPROFILE%/.okcode/AGENTS.md`

指令文件可以使用 `@include relative/path.md` 引用项目内的补充说明，但不能通过绝对路径或 `..` 跳出项目目录。

### 长期记忆

长期记忆保存在：

```text
<项目>/.okcode/memory/project/
<项目>/.okcode/memory/user/
```

每轮对话结束后，后台记忆 worker 会根据当前 Provider 更新索引；普通请求只注入受限长度的相关记忆。`.okcode/context/` 用于保存上下文压缩等运行时产物。

## MCP

MCP 配置与 Provider 配置分开管理：

| 范围 | 路径 |
| --- | --- |
| 用户级 | `%USERPROFILE%/.okcode/config.yaml` |
| 项目级 | `<项目>/.okcode/config.yaml` |

同名 Server 由项目级配置覆盖用户级配置。支持 `stdio` 和 `streamable_http`：

```yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: uvx
    args: ["some-mcp-server", "${WORKSPACE_ROOT}"]
    env:
      API_TOKEN: "${FILESYSTEM_TOKEN}"

  remote_search:
    transport: streamable_http
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer ${SEARCH_TOKEN}"
```

`args`、`env`、`url` 和 `headers` 支持 `${ENV_VAR}` 展开。未定义变量会导致配置错误，但不会输出变量值。MCP 工具名称统一为 `mcp__<server>__<tool>`。单个 Server 发现失败只产生告警，当前版本不会自动重连。

## Skill

Skill 用于向模型提供可复用的 SOP。启动时只加载名称和描述，模型需要调用 `load_skill` 才会读取完整内容，这样可以减少默认上下文。

Skill 来源和优先级：

| 来源 | 路径 |
| --- | --- |
| 内置 | `src/okcode/skills/builtin/` |
| 用户级 | `%USERPROFILE%/.okcode/skills/` |
| 项目级 | `<项目>/.okcode/skills/` |

有效 Skill 会生成同名动态命令，例如 `/commit`、`/test`。Skill 激活后使用当前会话快照，修改文件不会自动改变已经激活的 SOP；使用 `/clear` 或重新加载后才会切换版本。

## 子 Agent 与 Worktree

内置角色：

- `general-purpose`：通用代码任务；
- `code-reviewer`：只读审查和风险分析；
- `researcher`：只读检索和事实整理。

子 Agent 支持两种启动方式：

- `defined`：使用一个已定义角色；
- `fork`：基于当前会话快照启动。

执行模式可以是前台、后台或自动选择。后台任务使用 `/tasks` 查看、转后台或取消。

文件隔离模式：

- `shared`：与父会话共用当前工作区；
- `worktree`：在 `<项目>/.okcode/worktrees/` 下创建 Git worktree 和独立分支。

Worktree 会记录元数据，并在退出或过期清理前检查未提交修改、未推送提交和未跟踪文件，避免误删工作成果。

## Hooks

Hooks 配置文件：

```text
<项目>/.okcode/hooks.yaml
```

支持事件：

`session.start`、`session.end`、`turn.start`、`turn.end`、`message.user`、`message.assistant`、`tool.before`、`tool.after`、`system.context_compacted`、`system.error`。

支持动作：

- `prompt`：向下一次请求、当前轮次或整个会话注入提示；
- `shell`：执行受控 shell 命令，可在 `tool.before` 阶段拦截工具；
- `http`：向外部 HTTP 服务发送事件；
- `subagent`：在已配置 AgentLauncher 时启动后台子 Agent。

权限检查、参数校验和系统黑名单先于 Hook 生效。使用 `/hooks` 查看当前加载的规则、配置路径和最近执行状态。

## Team Lead

Team Lead 是持久化的长期团队协作模式。先进入一个团队：

```text
/team create demo
/team use demo
/team status
/team leave
```

进入团队后，主 Agent 作为 Lead 可以使用：

- `team_member`：创建、审批、唤醒、恢复和终止成员；
- `team_task`：创建、更新和查看共享任务；
- `team_message`：点对点发送、广播和读取邮箱消息；
- `team_merge`：查看成员分支并执行 Git 合并。

成员默认只能看到 `team_task` 和 `team_message`，用于处理自己的任务和回传结果。

### 成员执行流程

1. Lead 创建成员和共享任务；
2. Lead 发送 `task_assignment` 消息，运行时将消息写入成员邮箱并尝试唤醒成员；
3. 成员 worker 读取邮箱，启动 AgentRunner，在自己的工作目录中执行任务；
4. 成员更新任务状态和最近任务摘要，并向 Lead 发送 `completion` 或 `blocked` 消息；
5. Lead 读取共享状态和邮箱，汇总结果，必要时通过 `team_merge` 合并代码。

成员支持审批请求、任务依赖字段、恢复执行、点对点消息和广播。发送消息保持非阻塞；显式唤醒成员时可以等待当前任务执行结束。

### 团队目录

默认位置是当前项目目录：

```text
<项目>/.okcode/team/<team>/
├── team.json
├── members.json
├── tasks.json
├── registry.json
├── mailboxes/
└── member-sessions/
```

可以在 `config.yaml` 中通过 `team.teams_root` 覆盖根目录。团队状态、邮箱和成员摘要都以本地 JSON/JSONL 文件持久化，并使用 lock 文件保护并发写入。

### 成员后端

支持两种成员后端：

- `terminal_pane`：使用 Windows Terminal 或 tmux 的终端窗格，是否可用取决于当前环境；
- `coroutine`：在 OkCode 进程内运行，始终可用。

后端按 `team.terminal_backend_priority` 选择；显式要求不可用后端时会直接报错，不会静默降级。

### Coordinator 双锁模式

Coordinator 模式必须同时满足配置和环境变量：

```yaml
team:
  coordinator_enabled: true
```

```powershell
$env:OKCODE_COORDINATOR = "1"
uv run okcode
```

启用后，Lead 负责拆解任务、派发成员、审批、跟踪消息和合并代码：

- 隐藏 `write_file`、`edit_file`；
- 保留读工具、团队工具和受控 `run_command`；
- `run_command` 会拒绝明显的 shell 写文件命令，但允许 `git status`、`git diff`、`git merge`、`git branch` 等协作命令。

## 隐私与本地数据

以下内容属于用户运行状态或隐私数据，默认已加入 `.gitignore`：

```text
config.yaml
sessions/
.okcode/context/
.okcode/memory/
.okcode/team/
.okcode/worktrees/
.okcode/permissions.local.yaml
```

可以提交的项目级配置通常是 `.okcode/permissions.yaml`、`.okcode/hooks.yaml`、`.okcode/skills/` 和 `.okcode/agents/`。提交前请确认没有把 API Key、会话内容、长期记忆或团队邮箱带入 Git。

## 开发与验证

安装开发依赖：

```powershell
uv sync --all-groups
```

运行测试、代码检查和补丁检查：

```powershell
uv run pytest -q
uv run ruff check .
git diff --check
```

项目入口：

- `src/okcode/`：应用、会话、Provider、工具、权限、MCP、Skill、Agent、Team 等实现；
- `tests/unit/`：单元测试；
- `tests/integration/`：跨模块集成测试；
- `docs/`：阶段设计文档和验收记录。

## 当前边界

当前版本明确不覆盖：

- 跨机器或跨网络的分布式团队；
- 成员之间的实时流式通信；
- 复杂的自动任务依赖调度器；
- 完整的成员历史会话重放；
- 独立终端进程的自动重启和跨机器恢复。

这些边界不影响本地单项目中的 Agent Loop、MCP、Skill、子 Agent、Worktree 和 Team Lead 基本协作流程。
