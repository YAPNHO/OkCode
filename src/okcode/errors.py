"""面向用户的安全错误类型。"""

from enum import StrEnum


class OkCodeError(Exception):
    """OkCode 可预期错误的基类。"""


class ConfigError(OkCodeError):
    """本地配置无效。"""


class ExitRequested(OkCodeError):
    """用户在嵌套交互提示中请求退出 REPL。"""


class ProviderErrorKind(StrEnum):
    """供应商错误类别。"""

    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    BAD_REQUEST = "bad_request"
    SERVER = "server"
    STREAM = "stream"


class ProviderError(OkCodeError):
    """已脱敏的供应商错误。"""

    def __init__(
        self,
        kind: ProviderErrorKind,
        safe_message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.kind = kind
        self.safe_message = safe_message
        self.status_code = status_code
        self.request_id = request_id
