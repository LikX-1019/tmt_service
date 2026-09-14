"""公共响应模型，统一所有接口的成功和失败数据结构。"""

from typing import Generic, TypeVar

from pydantic import BaseModel


DataT = TypeVar("DataT")


class ApiResponse(BaseModel, Generic[DataT]):
    """API 统一响应外壳，成功使用数字 0，失败使用稳定字符串错误码。"""

    code: int | str = 0
    message: str = "success"
    data: DataT | None = None
