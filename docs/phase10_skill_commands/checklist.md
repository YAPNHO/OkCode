# OkCode 第十阶段补充：Skill 斜杠命令 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦用户可见行为和快照一致性。

## Skill 命令发现与两阶段加载

- [x] 内置有效 `commit`、`test` Skill 同时出现在 `/skill`、`/help` 和斜杠补全中，且分别显示为 `/commit`、`/test`。（验证：运行 Skill、命令和终端相关测试，检查目录列表、可见命令及补全候选。）
- [x] 输入 `/commit 提交当前改动` 或 `/test 运行相关测试` 会生成明确指定对应 Skill 的 Agent 请求。（验证：命令处理测试断言转发消息含目标 Skill 名称和任务文本。）
- [x] 输入无参数的 `/commit` 仍请求使用 `commit` Skill，并保留默认任务语义。（验证：命令处理测试断言无参数转发消息。）
- [x] 动态命令本身不读取或显示 SOP；启动期提示词和 `/skill` 输出只包含名称、来源、说明和状态。（验证：提示词与目录输出测试断言完整 SOP 不在其中。）
- [x] 动态命令转发后仍由系统级 `load_skill` 加载完整 SOP，并沿用既有 shared/isolated 执行路径。（验证：`/commit` 到 `load_skill` 的会话测试先断言转发，再断言激活上下文包含完整 SOP。）

## 覆盖与命令冲突

- [x] 同名 Skill 同时存在于项目、用户、内置三层时，仅优先级最高的版本出现在目录和命令集合中。（验证：目录覆盖测试断言仅注册一个同名命令，且其来源为 `PROJECT > USER > BUILTIN` 的最终版本。）
- [x] 同一来源中出现同名 Skill 时，启动以可显示的配置错误失败。（验证：CLI 测试断言错误包含 Skill 名称和重复文件路径。）
- [x] 最终有效 Skill 与静态内置命令主名称或别名冲突时，启动在 Provider 创建前失败；错误含冲突命令名、Skill 路径和已有命令所有者。（验证：分别构造主名称与别名冲突的 CLI 测试。）
- [x] 动态 Skill 命令之间发生名称或别名冲突时，命令注册表拒绝替换且保留旧快照。（验证：注册表和运行时测试断言异常后 `resolve()`、可见命令和补全候选均保持刷新前结果。）

## 原子刷新与激活快照

- [x] 执行 `/skill` 和调用 `load_skill` 使用同一刷新事务；刷新成功后目录、`/help`、补全和命令分发看到同一 Skill 集合。（验证：新增或修改 Skill 后分别走两条入口，断言四处可见结果一致。）
- [x] 热更新新增有效 Skill 后，其命令可被补全和分发；删除或解析失败后，对应命令从目录、帮助和补全中移除。（验证：运行时刷新测试覆盖新增、删除和解析失败三种情况。）
- [x] 热更新出现命令冲突时，目录和动态命令均保留上一次成功快照；`/skill` 显示诊断，`load_skill` 返回带诊断的 `ToolFailure`。（验证：分别调用两个入口，断言旧 Skill、旧命令仍可解析且错误可定位。）
- [x] 成功或失败的目录刷新均不替换已激活 Skill 的 SOP 快照。（验证：先激活一个 Skill，修改其文件并刷新后断言激活上下文仍为刷新前内容；显式重新加载后才切换。）

## `/review` 回归

- [x] 内置 `review` Skill 不再出现在 `/skill`、帮助或补全中。（验证：内置目录发现测试和终端可见性测试断言只含 `commit`、`test`。）
- [x] 静态 `/review` 命令仍能被分发，且原有处理语义保持不变。（验证：既有 `/review` 命令处理和会话测试通过。）
- [x] 项目或用户目录的有效 `review` Skill 会与静态 `/review` 发生启动期冲突并报出路径。（验证：参数化 CLI 测试覆盖项目、用户两个来源。）

## 全量回归

- [x] Skill、命令、CLI、终端的聚焦单元测试全部通过。（验证：执行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_commands_handlers.py tests/unit/test_skills_catalog.py tests/unit/test_skills_activation.py tests/unit/test_skills_conversation.py tests/unit/test_skills_load_tool.py tests/unit/test_cli.py tests/unit/test_terminal.py -q`。）
- [x] 全量测试通过。（验证：执行 `uv run pytest -q`，退出码为 0。）
- [x] 格式、静态检查和 Git 空白检查通过。（验证：依次执行 `uv run ruff format --check .`、`uv run ruff check .`、`git diff --check`，退出码均为 0。）

## 端到端场景

- [x] 场景 1：启动后输入 `/skill` 看到 `commit` 与 `test`，输入 `/commit 提交已修改文件` 后 Agent 调用 `load_skill`，完整 SOP 只在激活后进入环境上下文，随后完成既有 shared/isolated 流程。（验证：端到端会话测试检查目录、转发、工具调用和激活上下文的顺序。）
- [x] 场景 2：已运行进程中新增一个合法 Skill 并刷新后可直接用同名命令；再改成与 `/review` 冲突后刷新失败，原命令与原 Skill 列表仍可用。（验证：运行时/加载工具测试检查成功刷新、失败刷新和旧快照连续可用。）
