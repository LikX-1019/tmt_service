from pathlib import Path

from scripts.dev_compose import (
    MAX_PROJECT_NAME_LENGTH,
    compose_command,
    derive_compose_project_name,
    resolve_project_name,
)


def test_worktrees_get_distinct_stable_project_names() -> None:
    main = Path("/tmp/tmt_service")
    feature = Path("/tmp/tmt_service-customer-demo")

    assert derive_compose_project_name(main) == derive_compose_project_name(main)
    assert derive_compose_project_name(main) != derive_compose_project_name(feature)


def test_project_name_is_compose_safe_and_bounded() -> None:
    project = derive_compose_project_name(Path("/tmp/Feature Worktree@" + "x" * 80))

    assert len(project) <= MAX_PROJECT_NAME_LENGTH
    assert project[0].isalnum()
    assert all(character.isalnum() or character in "_-" for character in project)


def test_explicit_project_name_takes_priority(monkeypatch) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "environment-name")

    assert resolve_project_name(Path("/tmp/worktree"), "explicit-name") == "explicit-name"
    assert resolve_project_name(Path("/tmp/worktree")) == "environment-name"


def test_compose_command_always_passes_project_name() -> None:
    assert compose_command("feature-project", ["config", "--quiet"]) == [
        "docker",
        "compose",
        "-p",
        "feature-project",
        "config",
        "--quiet",
    ]
