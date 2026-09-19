"""按当前操作系统安装每日 17:40 定时任务。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core.config import Settings


LAUNCHD_LABEL = "com.tmt.daily-worklog"


def launchd_plist_path() -> Path:
    """返回当前用户的 launchd 配置文件路径。"""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def render_launchd_plist(settings: Settings, uv_executable: Path) -> ET.ElementTree:
    """生成 macOS launchd 配置，明确固定 Asia/Shanghai 和 17:40。"""
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "generate_daily_worklog.py"
    root = ET.Element("plist", {"version": "1.0"})
    dict_node = ET.SubElement(root, "dict")

    def pair(key: str, value: str | None = None) -> None:
        ET.SubElement(dict_node, "key").text = key
        if value is not None:
            ET.SubElement(dict_node, "string").text = value

    def pair_list(key: str, values: list[str]) -> None:
        pair(key)
        array = ET.SubElement(dict_node, "array")
        for value in values:
            ET.SubElement(array, "string").text = value

    pair("Label", LAUNCHD_LABEL)
    pair_list(
        "ProgramArguments",
        [
            str(uv_executable),
            "run",
            "--project",
            str(project_root),
            "python",
            str(script_path),
        ],
    )
    pair("WorkingDirectory", str(project_root))
    pair("ProcessType", "Background")
    pair("StartCalendarInterval")
    interval = ET.SubElement(dict_node, "dict")
    ET.SubElement(interval, "key").text = "Hour"
    ET.SubElement(interval, "integer").text = str(settings.worklog_schedule_hour)
    ET.SubElement(interval, "key").text = "Minute"
    ET.SubElement(interval, "integer").text = str(settings.worklog_schedule_minute)
    pair("StandardOutPath", str(settings.log_dir / "worklog.launchd.out.log"))
    pair("StandardErrorPath", str(settings.log_dir / "worklog.launchd.error.log"))
    pair("EnvironmentVariables")
    environment = ET.SubElement(dict_node, "dict")
    for key, value in (
        ("TZ", settings.worklog_timezone),
        ("PYTHONDONTWRITEBYTECODE", "1"),
    ):
        ET.SubElement(environment, "key").text = key
        ET.SubElement(environment, "string").text = value
    return ET.ElementTree(root)


def install_launchd(settings: Settings) -> Path:
    """写入用户 LaunchAgent 并注册到当前 GUI session。"""
    if platform.system() != "Darwin":
        raise RuntimeError("launchd installation is only supported on macOS")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv executable is required for the launchd task")
    destination = launchd_plist_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree = render_launchd_plist(settings, Path(uv))
    tree.write(destination, encoding="UTF-8", xml_declaration=True)
    uid = os.getuid()
    service = f"gui/{uid}/{LAUNCHD_LABEL}"
    subprocess.run(["launchctl", "bootout", service], check=False, capture_output=True)
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["launchctl", "enable", service], check=True, capture_output=True)
    return destination
