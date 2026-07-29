"""Provider 工厂。"""

from okcode.models import ProviderConfig, ProviderProtocol
from okcode.providers.anthropic import AnthropicProvider
from okcode.providers.base import LLMProvider
from okcode.providers.openai import OpenAIProvider


def create_provider(config: ProviderConfig) -> LLMProvider:
    """根据协议创建对应的 Provider。"""

    if config.protocol is ProviderProtocol.OPENAI:
        return OpenAIProvider(config)
    if config.protocol is ProviderProtocol.ANTHROPIC:
        return AnthropicProvider(config)
    raise ValueError(f"不支持的协议：{config.protocol}")
