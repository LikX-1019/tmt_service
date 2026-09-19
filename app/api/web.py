"""无需 Node 构建的本地客服控制台与测试页面。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(include_in_schema=False)
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


@router.get("/", response_class=FileResponse)
async def console_page() -> FileResponse:
    """返回拼多多客服三栏控制台。"""
    return FileResponse(WEB_ROOT / "console" / "index.html", media_type="text/html")


@router.get("/chat-demo", response_class=FileResponse)
async def chat_demo_page() -> FileResponse:
    """保留原聊天测试页面。"""
    return FileResponse(WEB_ROOT / "index.html", media_type="text/html")


@router.get("/customer-demo", response_class=FileResponse)
async def customer_demo_page() -> FileResponse:
    """返回模拟真实电商客户聊天页面。"""
    return FileResponse(
        WEB_ROOT / "customer-demo" / "index.html", media_type="text/html"
    )


@router.get("/service-hub", response_class=FileResponse)
async def service_hub_page() -> FileResponse:
    """返回本地服务导航与进程监控页面。"""
    return FileResponse(WEB_ROOT / "service-hub" / "index.html", media_type="text/html")


@router.get("/assets/console/{asset_name}", response_class=FileResponse)
async def console_asset(asset_name: str) -> FileResponse:
    """仅公开控制台固定静态资源，避免任意路径读取。"""
    if asset_name not in {"app.css", "app.js"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)
    media_type = "text/css" if asset_name.endswith(".css") else "text/javascript"
    return FileResponse(WEB_ROOT / "console" / asset_name, media_type=media_type)


@router.get("/assets/service-hub/{asset_name}", response_class=FileResponse)
async def service_hub_asset(asset_name: str) -> FileResponse:
    """仅公开服务导航页固定静态资源，避免任意路径读取。"""
    if asset_name not in {"app.css", "app.js"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)
    media_type = "text/css" if asset_name.endswith(".css") else "text/javascript"
    return FileResponse(
        WEB_ROOT / "service-hub" / asset_name,
        media_type=media_type,
    )


@router.get("/assets/customer-demo/{asset_name}", response_class=FileResponse)
async def customer_demo_asset(asset_name: str) -> FileResponse:
    """仅公开 Customer Demo 固定静态资源，避免任意路径读取。"""
    if asset_name not in {"app.css", "app.js"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)
    media_type = "text/css" if asset_name.endswith(".css") else "text/javascript"
    return FileResponse(
        WEB_ROOT / "customer-demo" / asset_name, media_type=media_type
    )


@router.get("/qa-demo", response_class=FileResponse)
async def qa_demo_page() -> FileResponse:
    """返回可查看真实召回 QA 对的检索演示页面。"""
    return FileResponse(WEB_ROOT / "qa-demo.html", media_type="text/html")
