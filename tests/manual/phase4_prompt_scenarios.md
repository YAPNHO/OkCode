# Phase 4 人工对比场景

## 运行前置

- 使用包含当前阶段代码的测试工作区。
- 准备至少一个支持缓存 usage 字段的 Provider；如需启用缓存路由，在对应 Provider 配置中设置 prompt_cache: true。
- 记录 Provider 名称、模型、是否开启 prompt_cache、时间和工作区路径。
- 本阶段不依赖真实 MCP、项目指令文件、自动记忆或自动评分器。

## 记录模板

~~~text
Provider：
模型：
prompt_cache：
工作区：
时间：
用户输入：
工具调用顺序：
最终回答摘要：
稳定 cache_key：
缓存用量（read/write/available）：
观察结论：
~~~

## 场景 1：读文件前置

**输入：** 请解释 src/okcode/conversation.py 中 /plan 的执行流程。  
**步骤：**

1. 启动 OkCode，提交输入。
2. 记录第一项工具调用及其参数。
3. 记录最终回答是否引用真实文件路径和代码事实。

**观察点：**

- 第一项工具调用应为 read_file，或先用 search_code 定位后再 read_file。
- 回答不应凭空描述未读取的实现细节。

## 场景 2：代码搜索

**输入：** 找出 TokenUsage 在哪里构造，并说明缓存命中字段如何传到终端。  
**步骤：**

1. 提交输入。
2. 记录是否优先使用 search_code 或 find_files。
3. 记录返回的路径、行号和最终解释。

**观察点：**

- 模型应依据搜索结果定位 models.py、conversation.py 或 Provider 文件。
- 不应编造不存在的路径或字段。

## 场景 3：文件编辑前读取

**输入：** 将 tests/manual/phase4_prompt_scenarios.md 的记录模板增加一行“验证命令”。  
**步骤：**

1. 在可恢复的测试副本中提交输入。
2. 记录 edit_file 或 write_file 前的工具调用。
3. 检查文件的实际差异。

**观察点：**

- 编辑前应有 read_file 或足以确认目标原文的搜索证据。
- 修改应只影响目标模板，不覆盖无关内容。

## 场景 4：规划与执行

**输入 1：** /plan 为 Phase 4 新增缓存字段补齐测试。  
**输入 2：** /do  
**步骤：**

1. 提交输入 1，记录可见工具和规划结果。
2. 确认规划阶段没有 write_file、edit_file 或 run_command。
3. 提交输入 2，确认会话使用已保存计划并开放全量工具。

**观察点：**

- /plan 只使用只读工具，最终结果是含文件、步骤和验证方式的计划。
- /do 的用户任务来自已保存计划，系统补充规则强调先读后改、验证后报告。

## 场景 5：动态环境变化

**输入：** 两次分别询问“当前工作区和可用工具是什么？”，两次之间只改变日期或工作区状态。  
**步骤：**

1. 通过可控测试替身或调试记录取得两次 ProviderRequest。
2. 记录 stable_system、dynamic_system 和 cache_key。
3. 比较两次结果。

**观察点：**

- stable_system 和 cache_key 应保持相同。
- 环境补充文本应反映日期、工作区或工具状态变化。
- 环境补充带 okcode-system-note 标签，不进入普通用户历史。

## 场景 6：缓存命中观测

**输入：** 连续运行两次相同的代码阅读任务。  
**步骤：**

1. 使用支持缓存 usage 的 Provider，记录第一次和第二次的 TokenUsageReported。
2. 记录 input、output、cache read、cache write 和 cache available。
3. 如果 Provider 没有返回缓存字段，记录为不可用，而不是 0 命中。

**观察点：**

- OpenAI 兼容请求读取 cached_tokens 时映射为 cache read。
- Anthropic 的 cache_read_input_tokens 和 cache_creation_input_tokens 分别映射为 read/write。
- 不得把缺失字段解释为缓存命中 0。
