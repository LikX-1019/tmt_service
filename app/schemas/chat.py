"""聊天接口请求和响应模型，并在入口完成消息基础校验。"""

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """聊天请求：自动清理首尾空白，并限制消息长度。"""
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    """聊天成功响应中的业务数据。"""
    answer: str
