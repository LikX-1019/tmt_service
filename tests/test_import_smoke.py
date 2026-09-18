"""Smoke checks for required modules and the QA service dependency boundary."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


REQUIRED_MODULES = (
    "main",
    "app.agent.greeting",
    "app.agent.routing",
    "app.services.shop_runtime_manager",
    "app.core.lifecycle",
)


@pytest.mark.parametrize("module_name", REQUIRED_MODULES)
def test_required_modules_import(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_qa_service_direct_dependencies_stay_channel_agnostic() -> None:
    module = importlib.import_module("app.qa.service")
    source_path = Path(module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    internal_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            internal_modules.update(
                alias.name for alias in node.names if alias.name.startswith("app.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("app."):
                internal_modules.add(node.module)

    forbidden = {
        module
        for module in internal_modules
        if module.startswith(("app.services.console_runtime", "app.integrations.pdd"))
    }
    assert not forbidden
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "fastapi"
        for node in ast.walk(tree)
    )
