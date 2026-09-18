"""业务异常定义，用稳定错误码映射统一的 API 错误响应。"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """业务异常基类，统一携带错误码、提示信息和 HTTP 状态码。"""

    code = "APP_ERROR"
    default_message = "服务暂时不可用"
    status_code = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化对外提示和仅供内部诊断使用的异常详情。"""
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class UnsupportedLLMProviderError(AppException):
    """模型服务提供商不受支持时抛出的配置异常。"""
    code = "UNSUPPORTED_LLM_PROVIDER"
    default_message = "不支持的大模型服务提供商"
    status_code = 500


class LLMInvocationError(AppException):
    """模型调用、连接或响应解析失败时抛出的业务异常。"""
    code = "LLM_INVOCATION_ERROR"
    default_message = "大模型调用失败，请稍后重试"
    status_code = 502


class InvalidRequestError(AppException):
    """业务层发现请求内容无效时抛出的异常。"""
    code = "INVALID_REQUEST"
    default_message = "请求参数无效"
    status_code = 400


class ConversationStateUnavailableError(AppException):
    """会话商品上下文数据库不可用。"""

    code = "CONVERSATION_STATE_UNAVAILABLE"
    default_message = "会话上下文暂时不可用"
    status_code = 503


class ProductServiceUnavailableError(AppException):
    """商品资料服务不可用。"""

    code = "PRODUCT_SERVICE_UNAVAILABLE"
    default_message = "商品资料暂时不可用，请稍后重试"
    status_code = 503


class ConsoleUnavailableError(AppException):
    """控制台数据库或浏览器连接器不可用。"""

    code = "CONSOLE_UNAVAILABLE"
    default_message = "客服控制台暂时不可用"
    status_code = 503


class ResourceNotFoundError(AppException):
    """请求的店铺、会话或任务不存在。"""

    code = "RESOURCE_NOT_FOUND"
    default_message = "请求的资源不存在"
    status_code = 404


class ShopContextRequiredError(AppException):
    """多店状态下旧接口无法安全推断店铺。"""

    code = "SHOP_CONTEXT_REQUIRED"
    default_message = "存在多个店铺，请明确选择店铺"
    status_code = 409


class ShopLimitExceededError(AppException):
    """同时在线店铺已达到本机配置上限。"""

    code = "SHOP_LIMIT_EXCEEDED"
    default_message = "同时在线店铺数量已达到上限"
    status_code = 409


class DuplicateShopAccountError(AppException):
    """新窗口登录了已经接入的平台账号。"""

    code = "DUPLICATE_SHOP_ACCOUNT"
    default_message = "该拼多多店铺已经接入"
    status_code = 409
