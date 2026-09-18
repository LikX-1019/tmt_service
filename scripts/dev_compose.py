"""Run Docker Compose with a stable project name scoped to the current worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_PROJECT_NAME_LENGTH = 48


def derive_compose_project_name(worktree: Path) -> str:
    raw_name = worktree.resolve().name.lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw_name).strip("-")
    if not slug:
        slug = "tmt-worktree"
    if len(slug) <= MAX_PROJECT_NAME_LENGTH:
        return slug
    digest = hashlib.sha256(str(worktree.resolve()).encode()).hexdigest()[:8]
    prefix = slug[: MAX_PROJECT_NAME_LENGTH - len(digest) - 1].rstrip("-_")
    return f"{prefix}-{digest}"


def resolve_project_name(worktree: Path, explicit: str | None = None) -> str:
    return explicit or os.environ.get("COMPOSE_PROJECT_NAME") or derive_compose_project_name(worktree)


def compose_command(project_name: str, arguments: list[str]) -> list[str]:
    return ["docker", "compose", "-p", project_name, *arguments]


def _run_text(command: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _load_compose_config(project_name: str, env: dict[str, str]) -> dict[str, Any]:
    output = _run_text(
        compose_command(project_name, ["config", "--format", "json"]),
        env,
    )
    return json.loads(output)


def _load_running_containers(project_name: str, env: dict[str, str]) -> dict[str, str]:
    output = _run_text(compose_command(project_name, ["ps", "--format", "json"]), env)
    containers: dict[str, str] = {}
    for line in output.splitlines():
        if line.strip():
            item = json.loads(line)
            containers[item["Service"]] = item["Name"]
    return containers


def show_info(project_name: str, env: dict[str, str]) -> None:
    config = _load_compose_config(project_name, env)
    containers = _load_running_containers(project_name, env)
    branch = _run_text(["git", "branch", "--show-current"], env).strip() or "(detached)"

    print(f"Compose project: {project_name}")
    print(f"Worktree: {PROJECT_ROOT}")
    print(f"Branch: {branch}")
    print("Containers:")
    for service in sorted(config.get("services", {})):
        print(f"  {service}: {containers.get(service, '(not created)')}")
    print("Networks:")
    for key, network in sorted(config.get("networks", {}).items()):
        print(f"  {key}: {network.get('name', key)}")
    print("Data mounts:")
    for service, definition in sorted(config.get("services", {}).items()):
        for mount in definition.get("volumes", []):
            if mount.get("type") == "bind" and mount.get("target") in {
                "/var/lib/mysql",
                "/var/lib/postgresql/data",
                "/etcd",
                "/data",
                "/var/lib/milvus",
            }:
                print(f"  {service}: {mount['source']} -> {mount['target']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Docker Compose wrapper with worktree-scoped project naming."
    )
    parser.add_argument("--project-name", help="override the generated Compose project name")
    parser.add_argument("compose_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.compose_args:
        build_parser().print_help()
        return 2

    project_name = resolve_project_name(PROJECT_ROOT, args.project_name)
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = project_name
    if args.compose_args == ["info"]:
        show_info(project_name, env)
        return 0

    result = subprocess.run(
        compose_command(project_name, args.compose_args),
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
