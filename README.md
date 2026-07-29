# OkCode

OkCode 是一个使用 Python 实现的终端 AI 编程助手。它在当前工作目录内协助阅读代码、定位文件、修改文本、执行命令和运行验证；模型需要调用工具时，OkCode 会继续执行 Agent Loop，直到得到正式回答或触发安全上限。

当前会话历史仅保存在内存中，退出进程后会清空。

## 已实现能力

- 支持 OpenAI 兼容协议和 Anthropic Messages 协议，可通过 YAML 切换 Provider 与模型。
- 支持流式回答、可选的思考内容展示，以及每次模型调用的 Token 用量展示。
- 内置 Agent Loop：支持一轮中的多个工具调用，只读工具可并行执行，有副作用的操作按顺序执行；连续工具调用最多进行 12 次模型迭代。
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

## 交互命令

| 输入 | 行为 |
| --- | --- |
| 普通自然语言任务 | 模型可使用全部六项工具完成阅读、修改和验证。 |
| `/plan <任务>` | 进入规划模式，只允许读取、查找和搜索工具；成功后在当前会话保存计划。 |
| `/do` | 执行当前会话最近一次已保存计划；没有计划时不会执行操作。 |
| `/exit` 或 EOF | 退出 OkCode。 |

输入阶段按 `Ctrl+C` 会清空当前输入；生成阶段按 `Ctrl+C` 会取消本轮，已取消内容不会写入后续会话历史。

## 开发验证

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```

## 当前范围

当前版本尚未接入真实 MCP、项目指令文件加载、自动长期记忆和自动化效果评估。这些能力在提示结构中保留了扩展位置，但不会在运行时自动生效。
