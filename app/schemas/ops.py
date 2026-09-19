"""本地服务导航与进程监控接口模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ServiceStatusView(BaseModel):
    service_name: str
    service_state: str = "unknown"
    service_health: str = "unknown"
    container_name: str | None = None
    status_text: str | None = None
    published_ports: list[str] = Field(default_factory=list)


class EndpointStatusView(BaseModel):
    endpoint_id: str
    display_name: str
    endpoint_url: str
    is_ok: bool = False
    http_status: int | None = None
    latency_ms: int | None = None
    error_name: str | None = None


class ServiceLinkView(BaseModel):
    link_id: str
    link_title: str
    link_description: str
    link_url: str
    link_badge: str = "Web"


class OpsSnapshotView(BaseModel):
    application_pid: int
    application_running: bool = True
    compose_available: bool = True
    compose_error: str | None = None
    compose_services: list[ServiceStatusView] = Field(default_factory=list)
    http_endpoints: list[EndpointStatusView] = Field(default_factory=list)
    page_links: list[ServiceLinkView] = Field(default_factory=list)


ServiceTarget = Literal[
    "core",
    "all",
    "mysql",
    "product-postgres",
    "etcd",
    "minio",
    "milvus",
    "commodity-management",
    "attu",
]
ServiceAction = Literal["start", "stop", "restart"]


class OpsActionRequest(BaseModel):
    action: ServiceAction
    confirm: bool = False


class OpsActionResultView(BaseModel):
    control_target: str
    control_action: str
    success: bool
    result_detail: str


class ServiceControlRequest(BaseModel):
    target: ServiceTarget = "core"
    action: ServiceAction
