"""本地开发服务的只读监控和受控启停编排。

这些能力面向本机调试页面，不允许接收任意命令；Compose 服务名和动作都在白名单内。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from app.core.config import Settings, get_settings
from app.schemas.ops import (
    EndpointStatusView,
    OpsActionResultView,
    OpsSnapshotView,
    ServiceLinkView,
    ServiceStatusView,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_SERVICES = (
    "mysql",
    "product-postgres",
    "etcd",
    "minio",
    "milvus",
    "commodity-management",
)
OPTIONAL_SERVICES = ("attu",)
ALL_SERVICES = (*CORE_SERVICES, *OPTIONAL_SERVICES)
ServiceAction = Literal["start", "stop", "restart"]


@dataclass(slots=True)
class _CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class OpsService:
    """提供安全的本地 Compose 状态查询、依赖启停和应用进程控制。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._action_lock = threading.Lock()
        self._pending_application_action: str | None = None

    async def _run_compose(
        self,
        arguments: list[str],
        *,
        timeout: float = 45.0,
    ) -> _CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "compose",
                *arguments,
                cwd=PROJECT_ROOT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            return _CommandResult(127, "", str(exc))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return _CommandResult(124, "", "Docker Compose command timed out")
        return _CommandResult(
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _compose_services(self) -> tuple[list[ServiceStatusView], str | None]:
        result = await self._run_compose(["ps", "--format", "json"], timeout=12)
        if result.returncode != 0:
            return [], result.stderr.strip() or "Docker Compose is unavailable"

        payloads: list[dict[str, object]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, list):
                payloads.extend(item for item in decoded if isinstance(item, dict))
            elif isinstance(decoded, dict):
                payloads.append(decoded)

        by_name = {
            str(item.get("Service") or item.get("service")): item for item in payloads
        }
        services: list[ServiceStatusView] = []
        for name in ALL_SERVICES:
            item = by_name.get(name)
            if item is None:
                services.append(ServiceStatusView(service_name=name, service_state="not_created"))
                continue
            publishers = item.get("Publishers")
            ports: list[str] = []
            if isinstance(publishers, list):
                for publisher in publishers:
                    if not isinstance(publisher, dict):
                        continue
                    target = str(publisher.get("TargetPort") or "")
                    published = str(publisher.get("PublishedPort") or "")
                    if target and published and published != "0":
                        ports.append(f"{published}->{target}")
            services.append(
                ServiceStatusView(
                    service_name=name,
                    service_state=str(item.get("State") or "unknown"),
                    service_health=str(item.get("Health") or "unknown"),
                    container_name=str(item.get("Name") or "") or None,
                    status_text=str(item.get("Status") or "") or None,
                    published_ports=sorted(set(ports)),
                )
            )
        return services, None

    def _base_url(self, url: str) -> str:
        return url.rstrip("/")

    def _links(self) -> list[ServiceLinkView]:
        base = f"http://127.0.0.1:{self._settings.app_port}"
        commodity = self._base_url(self._settings.product_api_base_url)
        return [
            ServiceLinkView(
                link_id="console",
                link_title="客服控制台",
                link_description="多店铺、会话、消息与 PDD 连接器工作台。",
                link_url=f"{base}/",
                link_badge="Console",
            ),
            ServiceLinkView(
                link_id="customer-demo",
                link_title="Customer Demo",
                link_description="消费者侧统一 Chat API 调试页面。",
                link_url=f"{base}/customer-demo",
                link_badge="Chat",
            ),
            ServiceLinkView(
                link_id="qa-demo",
                link_title="QA Demo",
                link_description="查看 FAQ / RAG 召回证据与回答链路。",
                link_url=f"{base}/qa-demo",
                link_badge="QA",
            ),
            ServiceLinkView(
                link_id="chat-demo",
                link_title="原聊天测试页",
                link_description="保留的旧聊天页面，用于兼容对照。",
                link_url=f"{base}/chat-demo",
                link_badge="Legacy",
            ),
            ServiceLinkView(
                link_id="openapi",
                link_title="OpenAPI 文档",
                link_description="查看当前 FastAPI 全部接口定义。",
                link_url=f"{base}/docs",
                link_badge="API",
            ),
            ServiceLinkView(
                link_id="health",
                link_title="健康检查",
                link_description="主服务 lightweight health endpoint。",
                link_url=f"{base}/health",
                link_badge="Health",
            ),
            ServiceLinkView(
                link_id="commodity",
                link_title="商品资料管理",
                link_description="添加、编辑和发布商品资料；供商品问答读取。",
                link_url=commodity,
                link_badge="Product",
            ),
            ServiceLinkView(
                link_id="service-hub",
                link_title="服务导航与进程监控",
                link_description="本页面：集中打开入口并查看本地服务状态。",
                link_url=f"{base}/service-hub",
                link_badge="Hub",
            ),
        ]

    async def _endpoint_status(
        self,
        endpoint_id: str,
        label: str,
        url: str,
    ) -> EndpointStatusView:
        started = asyncio.get_running_loop().time()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
            latency = int((asyncio.get_running_loop().time() - started) * 1000)
            return EndpointStatusView(
                endpoint_id=endpoint_id,
                display_name=label,
                endpoint_url=url,
                is_ok=response.status_code == 200,
                http_status=response.status_code,
                latency_ms=latency,
            )
        except (httpx.HTTPError, OSError) as exc:
            latency = int((asyncio.get_running_loop().time() - started) * 1000)
            return EndpointStatusView(
                endpoint_id=endpoint_id,
                display_name=label,
                endpoint_url=url,
                latency_ms=latency,
                error_name=type(exc).__name__,
            )

    async def build_local_service_snapshot(self) -> OpsSnapshotView:
        base = f"http://127.0.0.1:{self._settings.app_port}"
        commodity = self._base_url(self._settings.product_api_base_url)
        compose_task = asyncio.create_task(self._compose_services())
        endpoint_tasks = [
            asyncio.create_task(self._endpoint_status("application", "主服务", f"{base}/health")),
            asyncio.create_task(self._endpoint_status("commodity", "商品资料服务", f"{commodity}/health")),
        ]
        services, compose_error = await compose_task
        endpoints = await asyncio.gather(*endpoint_tasks)
        return OpsSnapshotView(
            application_pid=os.getpid(),
            application_running=True,
            compose_available=compose_error is None,
            compose_error=compose_error,
            compose_services=services,
            http_endpoints=list(endpoints),
            page_links=self._links(),
        )

    def _service_names(self, target: str) -> list[str]:
        if target == "core":
            return list(CORE_SERVICES)
        if target == "all":
            return list(ALL_SERVICES)
        if target in ALL_SERVICES:
            return [target]
        raise ValueError("Unsupported service target")

    async def control_compose_services(
        self,
        *,
        target: str,
        action: ServiceAction,
    ) -> OpsActionResultView:
        names = self._service_names(target)
        if action == "start":
            arguments = ["up", "-d", *names]
            timeout = 120.0
        elif action == "restart":
            arguments = ["restart", *names]
            timeout = 90.0
        else:
            arguments = ["stop", *names]
            timeout = 60.0

        result = await self._run_compose(arguments, timeout=timeout)
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Docker Compose failed"
            return OpsActionResultView(
                control_target=target,
                control_action=action,
                success=False,
                result_detail=detail[:500],
            )
        return OpsActionResultView(
            control_target=target,
            control_action=action,
            success=True,
            result_detail=f"Docker Compose {action} completed: {', '.join(names)}",
        )

    def _schedule_shutdown(self, delay_seconds: float) -> None:
        timer = threading.Timer(
            delay_seconds,
            os.kill,
            args=(os.getpid(), signal.SIGTERM),
        )
        timer.daemon = True
        timer.start()

    def _spawn_replacement(self) -> None:
        uv_path = shutil.which("uv")
        if uv_path is None:
            raise RuntimeError("uv executable was not found")
        log_path = PROJECT_ROOT / "logs" / "service-restart.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            uv_path,
            "run",
            "uvicorn",
            "main:app",
            "--host",
            self._settings.app_host,
            "--port",
            str(self._settings.app_port),
        ]
        python_executable = Path(sys.executable)
        with log_path.open("a", encoding="utf-8") as log_file:
            subprocess.Popen(
                [
                    python_executable,
                    PROJECT_ROOT / "scripts" / "start_web_when_free.py",
                    str(os.getpid()),
                    self._settings.app_host,
                    str(self._settings.app_port),
                    *command,
                ],
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                check=False,
            )

    async def control_application_process(
        self,
        action: Literal["start", "stop", "restart"],
        *,
        confirm: bool,
    ) -> OpsActionResultView:
        if action == "start":
            return OpsActionResultView(
                control_target="application",
                control_action=action,
                success=True,
                result_detail="Application is already running because this API responded.",
            )
        if not confirm:
            return OpsActionResultView(
                control_target="application",
                control_action=action,
                success=False,
                result_detail="Confirmation is required.",
            )
        if self._settings.app_env == "production":
            return OpsActionResultView(
                control_target="application",
                control_action=action,
                success=False,
                result_detail="Application process control is disabled in production.",
            )

        with self._action_lock:
            if self._pending_application_action is not None:
                return OpsActionResultView(
                    control_target="application",
                    control_action=action,
                    success=False,
                    result_detail=f"Another {self._pending_application_action} action is pending.",
                )
            if action == "restart":
                try:
                    self._spawn_replacement()
                except (OSError, RuntimeError) as exc:
                    return OpsActionResultView(
                        control_target="application",
                        control_action=action,
                        success=False,
                        result_detail=str(exc),
                    )
                delay = 1.0
                detail = "Replacement scheduled; current process will stop gracefully."
            else:
                delay = 0.5
                detail = "Application will stop gracefully."
            self._pending_application_action = action

        self._schedule_shutdown(delay)
        return OpsActionResultView(
            control_target="application",
            control_action=action,
            success=True,
            result_detail=detail,
        )
