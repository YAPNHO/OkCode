---
name: commit
description: 提交前检查改动并生成提交信息
tools: [read_file, search_code, run_command]
mode: shared
history: recent
model: null
---

你正在执行 commit Skill。

请按以下 SOP 工作：
1. 先用只读方式查看当前改动范围和关键文件。
2. 运行与改动风险匹配的测试或静态检查。
3. 总结改动内容、风险点和验证结果。
4. 生成一条简洁中文提交信息。

不要直接提交代码，除非用户明确要求。
