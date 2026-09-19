"""本地服务导航页使用的安全受控运维接口。"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.schemas.common import ApiResponse
from app.schemas.ops import (
    OpsActionRequest,
    OpsActionResultView,
    OpsSnapshotView,
    ServiceControlRequest,
)
from app.services.ops_service import OpsService


router = APIRouter(prefix="/ops", tags=["ops"])
LOCAL_HOSTS = {"127.0.0.1", "::1"}


_local_ops_service = OpsService()


async def require_local_client(request: Request) -> None:
    client = request.client
    if client is None or client.host not in LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service hub APIs are local-only.",
        )


async def require_local_action(request: Request) -> None:
    await require_local_client(request)
    if request.headers.get("X-Service-Hub") != "local":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing local service hub header.",
        )


@router.get(
    "/snapshot",
    response_model=ApiResponse[OpsSnapshotView],
    dependencies=[Depends(require_local_client)],
)
async def read_local_service_snapshot(
) -> ApiResponse[OpsSnapshotView]:
    return ApiResponse(data=await _local_ops_service.build_local_service_snapshot())


@router.post(
    "/services",
    response_model=ApiResponse[OpsActionResultView],
    dependencies=[Depends(require_local_action)],
)
async def control_local_compose_services(
    request: ServiceControlRequest,
) -> ApiResponse[OpsActionResultView]:
    try:
        result = await _local_ops_service.control_compose_services(
            target=request.target,
            action=request.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ApiResponse(data=result)


@router.post(
    "/application",
    response_model=ApiResponse[OpsActionResultView],
    dependencies=[Depends(require_local_action)],
)
async def control_local_application(
    request: OpsActionRequest,
) -> ApiResponse[OpsActionResultView]:
    return ApiResponse(
        data=await _local_ops_service.control_application_process(
            request.action,
            confirm=request.confirm,
        )
    )
