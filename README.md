# OkCode

OkCode 是一个用 Python 编写的终端 AI 编程助手。第一阶段提供 YAML 配置、流式多轮对话、OpenAI/DeepSeek 与 Anthropic Provider，以及思考内容展示。

## 安装

```powershell
uv sync --all-groups
```

## 配置

在项目根目录创建 `config.yaml`，即 `D:\aaaaaaaa\Okcode\OkCode\config.yaml`。结构可参考 [config.example.yaml](config.example.yaml)。

DeepSeek V4 Pro 使用 OpenAI 协议，`thinking: true` 会请求并展示流式 reasoning 内容；OkCode 不会发送 reasoning 强度参数。Claude 的 `thinking: true` 会启用 extended thinking。

顶层 `active` 指向本次启动使用的配置名。修改它并重新启动即可切换供应商。

## 运行

```powershell
uv run okcode
```

也可以执行：

```powershell
uv run python -m okcode
```

输入 `/exit` 或发送 EOF 可以退出。输入阶段 Ctrl+C 清空当前输入；生成阶段 Ctrl+C 取消本轮，已取消内容不会写入后续对话历史。会话只保留在当前进程中，退出后清空。
