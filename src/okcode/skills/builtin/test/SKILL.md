---
name: test
description: 运行测试、定位失败并给出修复路径
tools: [read_file, search_code, run_command]
mode: shared
history: recent
model: null
---

你正在执行 test Skill。

请按以下 SOP 工作：
1. 根据任务范围选择最小有效测试命令。
2. 先运行测试并记录真实失败输出。
3. 定位失败对应的源码、测试或环境原因。
4. 用户要求修复时再修改代码；否则只报告原因和建议。
5. 修复后重新运行失败测试，再按风险扩大回归范围。
