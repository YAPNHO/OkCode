"""受控摘要请求构造和正式摘要提取。"""

from __future__ import annotations

from okcode.context.models import SummaryPlan
from okcode.models import ChatMessage, ProviderRequest, Role
from okcode.prompt import PromptBundle, PromptCachePolicy

_DRAFT_OPEN = "<analysis_draft>"
_DRAFT_CLOSE = "</analysis_draft>"
_FINAL_OPEN = "<formal_summary>"
_FINAL_CLOSE = "</formal_summary>"
_USER_MESSAGES_PLACEHOLDER = "{{ALL_USER_MESSAGES}}"
SUMMARY_HEADINGS = (
    "主要请求和意图",
    "关键技术概念",
    "文件和代码段",
    "错误和修复",
    "问题解决过程",
    "所有用户消息",
    "待办任务",
    "当前工作",
    "可能的下一步",
)

_SUMMARY_SYSTEM_PROMPT = """你是 OkCode 的内部上下文摘要器。
此请求绝对禁止调用工具；不得输出工具调用，
也不得声称已经读取未出现在输入中的文件或执行未出现的命令。

请先在 <analysis_draft> 与 </analysis_draft> 内写分析草稿。草稿仅供内部使用，会被程序丢弃。
随后在 <formal_summary> 与 </formal_summary> 内输出正式摘要。
正式摘要必须按以下九个 Markdown 二级标题依次组织，且不得遗漏任何标题：
## 主要请求和意图
## 关键技术概念
## 文件和代码段
## 错误和修复
## 问题解决过程
## 所有用户消息
{{ALL_USER_MESSAGES}}
## 待办任务
## 当前工作
## 可能的下一步

“当前工作”必须是最详细的部分。不要把不确定的代码、命令或工具结果写成已验证事实。
“所有用户消息”标题下必须原样保留占位符 {{ALL_USER_MESSAGES}}，该部分由程序填充用户原文。"""


class SummaryRequestFactory:
    """生成无工具、无缓存的内部摘要请求。"""

    def build(self, plan: SummaryPlan) -> ProviderRequest:
        """将待摘要转录传给独立摘要提示。"""

        prompt = PromptBundle(
            stable_system=_SUMMARY_SYSTEM_PROMPT,
            dynamic_system=(),
            debug_full_prompt=_SUMMARY_SYSTEM_PROMPT,
            cache_key="context-summary",
        )
        return ProviderRequest(
            messages=(ChatMessage(Role.USER, plan.transcript),),
            tools=(),
            prompt=prompt,
            cache=PromptCachePolicy(enabled=False),
        )

    def extract_final_summary(self, response_text: str, plan: SummaryPlan) -> str:
        """丢弃分析草稿，校验正式九段并确定性写入用户原文。"""

        if not response_text.strip():
            raise ValueError("摘要模型未返回正式内容。")
        draft_start = response_text.find(_DRAFT_OPEN)
        draft_end = response_text.find(_DRAFT_CLOSE)
        final_start = response_text.find(_FINAL_OPEN)
        final_end = response_text.find(_FINAL_CLOSE)
        if (
            draft_start < 0
            or draft_end < draft_start
            or final_start < 0
            or final_end < final_start
            or draft_end > final_start
        ):
            raise ValueError("摘要响应缺少受控草稿或正式摘要分区。")
        summary = response_text[final_start + len(_FINAL_OPEN) : final_end].strip()
        headings = tuple(line.strip() for line in summary.splitlines() if line.startswith("## "))
        expected_headings = tuple(f"## {heading}" for heading in SUMMARY_HEADINGS)
        if headings != expected_headings:
            raise ValueError("正式摘要缺少规定的九个部分。")
        if summary.count(_USER_MESSAGES_PLACEHOLDER) != 1:
            raise ValueError("正式摘要缺少用户原文占位标记。")
        user_messages = "\n\n".join(plan.original_user_messages)
        return summary.replace(_USER_MESSAGES_PLACEHOLDER, user_messages)
