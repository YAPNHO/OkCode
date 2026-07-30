# OkCode

OkCode 是一个使用 Python 实现的终端 AI 编程助手。它在当前工作目录内协助阅读代码、定位文件、修改文本、执行命令和运行验证；模型需要调用工具时，OkCode 会继续执行 Agent Loop，直到得到正式回答或触发安全上限。

每次启动均创建新的短期会话，不会自动载入任何旧对话；但第一轮模型请求会加载项目指令和长期记忆索引。已完成的短期会话会写入项目内 JSONL 存档，只有输入 `/resume` 并选择会话后才恢复旧消息。

## 已实现能力

- 支持 OpenAI 兼容协议和 Anthropic Messages 协议，可通过 YAML 切换 Provider 与模型。
- 支持流式回答、可选的思考内容展示，以及每次模型调用的 Token 用量展示。
- 内置 Agent Loop：支持一轮中的多个工具调用，只读工具可并行执行，有副作用的操作按顺序执行；每轮用户请求最多允许 12 次模型自主工具循环，用户继续发起的新对话不累计这个上限。
- 内置六项受工作区边界约束的本地工具：
  - `read_file`：读取 UTF-8 文本文件。
  - `find_files`：按 glob 模式查找文件。
  - `search_code`：搜索文本并返回匹配位置。
  - `write_file`：创建或完整写入 UTF-8 文本文件。
  - `edit_file`：仅在原文唯一匹配时执行替换。
  - `run_command`：在当前工作区内执行命令。
- 支持计划与执行模式：`/plan <任务>` 只开放只读工具并保存计划；`/do` 执行当前会话最近一次已保存计划。
- 系统提示按身份、系统约束、任务模式、动作执行、工具使用、语气风格和文本输出七个稳定模块组织。环境和模式提示以系统级补充消息注入，不会混入普通对话历史。
- 支持可选的提示缓存路由，并在 Provider 返回缓存用量字段时显示缓存读取、缓存写入 Token；不会猜测或伪造缓存命中结果。
- 内置五层本地工具权限控制：Windows 高危命令黑名单、项目路径沙箱、分层 YAML 规则、会话权限模式和默认确认。权限拒绝会作为工具结果回灌模型，Agent Loop 可以调整策略继续执行。
- 支持通过 MCP 接入外部工具：启动时发现 stdio 或 Streamable HTTP Server 的工具，并以统一工具接口交给 Agent Loop 调用。

## 安装

前置要求：Python 3.12 及 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --all-groups
```

## 配置

在启动目录创建 `config.yaml`。该文件含 API Key，已被 `.gitignore` 忽略，不应提交到版本库。

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

`prompt_cache` 默认为 `false`。仅当所选 Provider 明确支持提示缓存时才应开启：OkCode 会把稳定的系统提示和工具声明作为缓存前缀，而把工作区、日期、可用工具和任务模式等动态信息放入系统级补充消息。缓存是否实际命中以 Provider 返回的 usage 字段为准。

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

发现到的工具名称为 `mcp__<server>__<tool>`，避免与内置工具或其他 Server 冲突。远端工具默认按有副作用处理，因此会继续经过现有权限规则和默认确认。单个 Server 无法连接、握手或发现失败时会显示启动告警，但不会阻止内置工具和其他 MCP Server 使用；本版本不会自动重连。

## 运行

在希望让 OkCode 操作的项目根目录运行：

```powershell
uv run okcode
```

也可以使用模块入口：

```powershell
uv run python -m okcode
```

当前工作目录会成为工具可访问的工作区根目录，工具不能访问该目录之外的路径。

## 会话、指令与长期记忆

启动时会创建一个惰性会话日志，首轮正式回答成功后才在 `<项目>/sessions/` 下创建 `<YYYYMMDD-HHMMSS-xxxx>.jsonl`。每条消息单独追加为一行 JSON，列表中的标题、消息数和最近更新时间均直接扫描日志得到，不维护额外 meta 文件。超过 30 天的日志会在启动和查询时清理。

输入 `/resume` 后，终端会显示当前项目的可恢复会话表。输入编号恢复该会话；按回车或输入 `/cancel` 则保持当前新会话不变。恢复会跳过损坏行，并在工具调用历史不完整时只保留最后一个合法消息边界。相隔较久的会话会在续聊的下一次模型请求前收到一次状态核对提醒。

手写项目指令按以下优先级合并，高优先级文本位于前面：

| 优先级 | 路径 |
| --- | --- |
| 高 | `<项目>/AGENTS.md` |
| 中 | `<项目>/.okcode/AGENTS.md` |
| 低 | `%USERPROFILE%/.okcode/AGENTS.md` |

指令文件可以使用单独一行的 `@include relative/path.md` 引用项目内文件。引用深度受限，循环、绝对路径、包含 `..` 的路径以及解析后跳出项目目录的符号链接均会被拒绝。

每轮 Agent 自然结束后，OkCode 在后台使用当前 Provider 更新长期记忆，不会阻塞终端继续输入。记忆统一存于项目内 `<项目>/.okcode/memory/`，其中项目级笔记位于 `project/`，用户级笔记位于 `user/`；笔记分为用户偏好、纠正反馈、项目知识和参考资料，每条均为带 frontmatter 的 Markdown。两份索引会在普通请求前注入上下文，并各自限制为 200 行和 25KB。本阶段不包含向量检索、RAG、团队同步或启动时自动恢复旧会话。

## 权限规则

OkCode 在每次本地工具实际执行前检查权限。检查顺序固定为：Windows 高危命令黑名单、项目路径沙箱、会话/本地/项目/用户规则、权限模式、人工确认。前两层和命中的 `deny` 规则都是终局拒绝，不能被 `allow` 模式、规则或人工确认放开。

文件和目录工具会先解析符号链接或 Windows 重解析点，再确认目标仍位于项目目录内。`run_command` 仍由当前 Windows shell 执行，因此它使用黑名单、规则、模式和人工确认做准入控制，并不提供操作系统级命令沙箱。

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
```

规则写成 `工具名(模式)` 或只写 `工具名`。`Bash(...)` 是 `run_command(...)` 的兼容别名；模式支持精确匹配和 glob 匹配。命令模式匹配完整命令文本；路径模式匹配解析后的项目相对路径，并统一使用 `/`。

## 交互命令

| 输入 | 行为 |
| --- | --- |
| 普通自然语言任务 | 模型可使用全部六项工具完成阅读、修改和验证。 |
| `/plan <任务>` | 进入规划模式，只允许读取、查找和搜索工具；成功后在当前会话保存计划。 |
| `/do` | 执行当前会话最近一次已保存计划；没有计划时不会执行操作。 |
| `/permissions` | 显示当前权限模式及用户、项目、本地规则文件路径。 |
| `/permissions strict` | 未命中规则时直接拒绝。 |
| `/permissions default` | 未命中规则时请求确认，默认模式。 |
| `/permissions allow` | 未命中规则时直接放行；黑名单、路径沙箱和 `deny` 规则仍会拒绝。 |
| `/resume` | 显示当前项目的可恢复会话并按编号选择；回车或 `/cancel` 取消。 |
| `/exit` 或 EOF | 退出 OkCode。 |

输入阶段按 `Ctrl+C` 会清空当前输入；生成阶段按 `Ctrl+C` 会取消本轮，已取消内容不会写入后续会话历史。

在 `default` 模式下，未命中规则的调用会显示工具与命令或项目相对路径。输入 `d` 拒绝、`o` 仅允许本次、`s` 允许本会话、`p` 永久允许。永久允许只会写入项目本地的 `permissions.local.yaml`；空输入、EOF、中断或无法识别的选择都会安全拒绝。

## 开发验证

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```

## 当前范围

当前版本不支持 MCP 资源、提示词、采样、根目录协商、OAuth 专用认证、健康检查和自动重连；图片、音频与嵌入资源等非文本工具结果也不会交给模型处理。向量检索、RAG、团队记忆同步和自动化效果评估仍在后续范围。
