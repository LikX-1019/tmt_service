"""服务健康检查接口，供本地验证和部署探针调用。"""

from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """返回进程存活状态；本阶段不探测外部模型或数据库。"""
    return {"status": "ok"}
