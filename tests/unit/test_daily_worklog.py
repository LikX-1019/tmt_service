from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.core.config import PROJECT_ROOT, Settings
from app.worklog.collector import collect_repository
from app.worklog.launcher import render_launchd_plist
from app.worklog.models import CommitRecord, FileChange, RepositorySnapshot, WorklogCollection
from app.worklog.service import generate_daily_worklog
from app.worklog.summarizer import (
    cluster_work_items,
    format_worklog,
    parse_llm_daily_summary,
)


def _run_git(repository: Path, *arguments: str, **environment: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **environment},
        text=True,
    )


def _commit(repository: Path, message: str, committed_at: datetime) -> None:
    formatted = committed_at.isoformat()
    _run_git(repository, "add", "-A")
    _run_git(
        repository,
        "-c",
        "user.name=Worklog Test",
        "-c",
        "user.email=worklog@example.com",
        "commit",
        "-m",
        message,
        GIT_AUTHOR_DATE=formatted,
        GIT_COMMITTER_DATE=formatted,
    )


def test_collector_filters_old_commits_and_reads_today_changes(tmp_path: Path) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    _run_git(repository, "init")
    tracked = repository / "app.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    timezone = ZoneInfo("Asia/Shanghai")
    # Use the next cutoff if the current test run is after 17:40 so that newly
    # created worktree files are genuinely inside the generated scan range.
    scheduled_day = datetime.now(timezone) + timedelta(days=1)
    end = scheduled_day.replace(hour=17, minute=40, second=0, microsecond=0)
    start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    _commit(repository, "old work", end - timedelta(days=1))
    tracked.write_text("value = 2\n", encoding="utf-8")
    _commit(repository, "feat: add today work", end - timedelta(minutes=1))

    staged = repository / "staged.py"
    staged.write_text("ready = true\n", encoding="utf-8")
    _run_git(repository, "add", staged.name)
    tracked.write_text("value = 3\n", encoding="utf-8")
    untracked = repository / "untracked.py"
    untracked.write_text("new = true\n", encoding="utf-8")
    recent_timestamp = (end - timedelta(minutes=1)).timestamp()
    for path in (staged, tracked, untracked):
        os.utime(path, (recent_timestamp, recent_timestamp))

    snapshot = collect_repository(repository, start, end)
    assert [commit.message for commit in snapshot.commits] == [
        "feat: add today work"
    ]
    assert {change.source for change in snapshot.changes} == {
        "staged",
        "unstaged",
        "untracked",
    }


def test_related_commits_are_clustered_into_one_work_item() -> None:
    start = datetime(2026, 9, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    commit_time = start.replace(hour=10)
    collection = WorklogCollection(
        date="2026-09-19",
        timezone="Asia/Shanghai",
        start_at=start,
        end_at=start.replace(hour=17, minute=40),
    )
    collection.repositories = [
        RepositorySnapshot(
            name="service",
            path="/tmp/service",
            commits=[
                CommitRecord(
                    repository="service",
                    sha="a",
                    committed_at=commit_time,
                    message="add rules",
                    files=["app/services/rules.py"],
                ),
                CommitRecord(
                    repository="service",
                    sha="b",
                    committed_at=commit_time + timedelta(minutes=5),
                    message="fix rules",
                    files=["tests/unit/test_rules.py"],
                ),
            ],
            changes=[
                FileChange(
                    repository="service",
                    path="tests/unit/test_rules.py",
                    status="M",
                    source="unstaged",
                    staged=False,
                    modified_at=commit_time,
                )
            ],
        )
    ]
    collection.items = cluster_work_items(collection)
    assert len(collection.items) == 1
    item = collection.items[0]
    assert item.title == "前置规则引擎"
    assert item.files == ["app/services/rules.py"]
    assert item.commits == ["a", "b"]
    assert "working-tree:unstaged:tests/unit/test_rules.py" in item.evidence


def test_numbered_daily_format_rejects_non_numbered_output() -> None:
    assert parse_llm_daily_summary("1.完成规则\n\n2.完成状态", 2) == [
        "完成规则",
        "完成状态",
    ]
    assert parse_llm_daily_summary("- 完成规则\n\n- 完成状态", 2) is None
    collection = WorklogCollection(
        date="2026-09-19",
        timezone="Asia/Shanghai",
        start_at=datetime(2026, 9, 19, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_at=datetime(2026, 9, 19, 17, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    rendered = format_worklog(collection, ["完成规则", "完成状态"])
    assert rendered.startswith("2026/9/19 今日工作内容\n\n1.完成规则\n\n2.完成状态")
    assert "- 完成规则" not in rendered
    empty = format_worklog(collection, [])
    assert "今日暂未检索到可确认的开发工作记录" in empty


def test_repeated_generation_overwrites_daily_and_latest_files(
    tmp_path: Path, monkeypatch
) -> None:
    class FixedSummary:
        def summarize(self, collection: WorklogCollection) -> str | None:
            return "1.完成已确认工作\n\n2.完成补充验证"

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    settings = Settings(
        _env_file=None,
        worklog_output_dir=tmp_path / "worklogs",
        worklog_workspace_roots=str(missing_root),
        log_dir=tmp_path / "logs",
    )
    day = datetime(2026, 9, 19, 12, tzinfo=ZoneInfo("Asia/Shanghai"))

    first, collection, _ = generate_daily_worklog(
        settings,
        day=day,
        summarizer=FixedSummary(),
    )
    second, _, _ = generate_daily_worklog(
        settings,
        day=day,
        summarizer=FixedSummary(),
    )

    assert first == second == tmp_path / "worklogs" / "2026-09-19.md"
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert (tmp_path / "worklogs" / "latest.md").exists()
    assert len(list((tmp_path / "worklogs").glob("*.md"))) == 2
    assert collection.commit_count == 0


def test_worklog_settings_default_to_1740_and_support_roots() -> None:
    settings = Settings(_env_file=None)
    assert settings.worklog_schedule_hour == 17
    assert settings.worklog_schedule_minute == 40
    assert settings.worklog_timezone == "Asia/Shanghai"
    settings = Settings(
        _env_file=None,
        worklog_workspace_roots=f"{PROJECT_ROOT},~/other\nrelative-repo",
    )
    assert settings.workspace_roots[0] == PROJECT_ROOT
    assert settings.workspace_roots[1] == Path.home() / "other"
    assert settings.workspace_roots[2] == PROJECT_ROOT / "relative-repo"


def test_launchd_schedule_is_rendered_at_1740(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        worklog_output_dir=tmp_path / "worklogs",
        log_dir=tmp_path / "logs",
    )
    tree = render_launchd_plist(settings, Path("/usr/local/bin/uv"))
    rendered = tree.getroot().findall(".//integer")
    assert [node.text for node in rendered] == ["17", "40"]


@pytest.mark.parametrize("value", [-1, 24])
def test_worklog_schedule_rejects_invalid_hour(value: int) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, worklog_schedule_hour=value)
