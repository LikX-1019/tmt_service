"""OpsService 的状态解析与安全命令编排单元测试。"""

from pathlib import Path

import pytest

from app.services.ops_service import CORE_SERVICES, OpsService


def _service() -> OpsService:
    return OpsService(settings=type("FakeSettings", (), {})())


@pytest.mark.asyncio
async def test_compose_services_parses_line_delimited_json_and_missing_services() -> None:
    service = _service()

    async def fake_run_compose(arguments, *, timeout):
        assert arguments == ["ps", "--format", "json"]
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    '{"Service":"mysql","Name":"mysql-1","State":"running","Health":"healthy","Status":"Up","Publishers":[{"TargetPort":3306,"PublishedPort":3306}]}\n'
                    '{"Service":"milvus","Name":"milvus-1","State":"exited","Health":"unhealthy"}\n'
                ),
                "stderr": "",
            },
        )()

    service._run_compose = fake_run_compose
    statuses, error = await service._compose_services()

    assert error is None
    assert [item.service_name for item in statuses] == [*CORE_SERVICES, "attu"]
    mysql = statuses[0]
    assert mysql.container_name == "mysql-1"
    assert mysql.service_state == "running"
    assert mysql.service_health == "healthy"
    assert mysql.published_ports == ["3306->3306"]
    product_postgres = statuses[1]
    assert product_postgres.service_state == "not_created"


@pytest.mark.asyncio
async def test_control_services_uses_whitelisted_compose_actions() -> None:
    service = _service()
    calls = []

    async def fake_run_compose(arguments, *, timeout):
        calls.append((arguments, timeout))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    service._run_compose = fake_run_compose
    result = await service.control_compose_services(target="core", action="stop")

    assert result.success is True
    assert calls[0][0] == ["stop", *CORE_SERVICES]
    assert calls[0][1] == 60.0


@pytest.mark.asyncio
async def test_control_services_rejects_unexpected_target() -> None:
    service = _service()
    with pytest.raises(ValueError, match="Unsupported service target"):
        await service.control_compose_services(target="unexpected", action="stop")


def test_restart_helper_is_a_project_script_with_fixed_entrypoint() -> None:
    path = Path("scripts/start_web_when_free.py")
    text = path.read_text(encoding="utf-8")
    assert "os.execv(command[0], command)" in text
    assert "socket.create_connection" in text


@pytest.mark.asyncio
async def test_application_restart_spawns_replacement_and_schedules_shutdown(monkeypatch):
    settings = type(
        "FakeSettings",
        (),
        {
            "app_env": "development",
            "app_host": "127.0.0.1",
            "app_port": 8000,
        },
    )()
    service = OpsService(settings=settings)
    spawned = []
    scheduled = []

    monkeypatch.setattr("shutil.which", lambda _name: "/opt/bin/uv")
    monkeypatch.setattr(
        "app.services.ops_service.subprocess.Popen",
        lambda command, **_kwargs: spawned.append(command),
    )
    monkeypatch.setattr(
        service,
        "_schedule_shutdown",
        lambda delay: scheduled.append(delay),
    )

    result = await service.control_application_process("restart", confirm=True)

    assert result.success is True
    assert scheduled == [1.0]
    assert len(spawned) == 1
    assert str(spawned[0][1]).endswith("start_web_when_free.py")
    assert spawned[0][5:] == [
        "/opt/bin/uv",
        "run",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
