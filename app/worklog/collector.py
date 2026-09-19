"""从多个 Git 工作区收集当天提交与未提交变更。"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from app.worklog.models import CommitRecord, FileChange, RepositorySnapshot


class GitWorkspaceError(RuntimeError):
    """指定目录不是可用 Git 工作区。"""


def _run_git(repository: Path, *arguments: str) -> str:
    """执行只读 Git 命令并统一收集错误。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise GitWorkspaceError("git command is unavailable") from error
    except subprocess.CalledProcessError as error:
        raise GitWorkspaceError(
            f"git failed in {repository}: {error.stderr.strip()}"
        ) from error
    return result.stdout


def validate_workspace(path: Path) -> Path:
    """确认目录存在且由 Git 管理，避免把普通目录误当作工作区。"""
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise GitWorkspaceError(f"workspace does not exist: {resolved}")
    top_level = _run_git(resolved, "rev-parse", "--show-toplevel").strip()
    if not top_level:
        raise GitWorkspaceError(f"not a git workspace: {resolved}")
    return Path(top_level)


def _null_entries(value: str) -> list[str]:
    """解析 Git -z 输出，保留空文件名边界之外的有效条目。"""
    entries = value.split("\0")
    if entries and entries[-1] == "":
        entries.pop()
    return entries


def _diff_files(repository: Path, staged: bool) -> list[tuple[str, str]]:
    """读取 name-status -z 输出，正确处理 rename/copy 的两个路径。"""
    arguments = (
        ["diff", "--cached", "--name-status", "-z"]
        if staged
        else ["diff", "--name-status", "-z"]
    )
    entries = _null_entries(_run_git(repository, *arguments))
    results: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        status = entries[index]
        if index + 1 >= len(entries):
            break
        path = entries[index + 1]
        results.append((status, path))
        index += 3 if status.startswith(("R", "C")) else 2
    return results


def _modified_time(repository: Path, relative_path: str) -> datetime | None:
    path = repository / relative_path
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        # 删除类变更没有 worktree stat；Git status 本身就是变更证据。
        return None


def _within_day(value: datetime | None, start: datetime, end: datetime) -> bool:
    """按本地时区过滤未提交改动，避免把历史脏文件计入今天。"""
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=start.tzinfo)
    return start <= value <= end


def collect_commits(
    repository: Path,
    repository_name: str,
    start: datetime,
    end: datetime,
) -> list[CommitRecord]:
    """读取时间范围内的提交；旧提交不会进入当天证据。"""
    try:
        _run_git(repository, "rev-parse", "--verify", "--quiet", "HEAD")
    except GitWorkspaceError:
        # 新 init 的仓库还没有第一个提交，不是扫描错误。
        return []
    raw = _run_git(
        repository,
        "log",
        f"--since={start.isoformat()}",
        f"--until={end.isoformat()}",
        "--date=iso-strict",
        r"--pretty=format:%x1e%H%x1f%cI%x1f%s%x1f%b%x1f",
        "--name-status",
    )
    records: list[CommitRecord] = []
    for entry in raw.split("\x1e"):
        if not entry.strip():
            continue
        fields = entry.split("\x1f")
        if len(fields) < 5:
            continue
        sha, committed_at, subject, body = fields[0:4]
        file_block = "".join(fields[5:]).strip("\n\r")
        files: list[str] = []
        statuses: list[str] = []
        for line in file_block.splitlines():
            status, separator, path = line.strip().partition("\t")
            if separator and path:
                statuses.append(status)
                files.append(path)
        timestamp = datetime.fromisoformat(committed_at)
        message = " ".join(part.strip() for part in (subject, body) if part.strip())
        shortstat = _run_git(
            repository,
            "show",
            "--format=",
            "--shortstat",
            "--no-renames",
            sha,
        ).strip()
        records.append(
            CommitRecord(
                repository=repository_name,
                sha=sha,
                committed_at=timestamp,
                message=message,
                files=files,
                statuses=statuses,
                shortstat=shortstat,
            )
        )
    return records


def collect_uncommitted(
    repository: Path,
    repository_name: str,
    start: datetime,
    end: datetime,
) -> list[FileChange]:
    """收集 staged、unstaged 和未跟踪变更，并尽量过滤旧脏文件。"""
    changes: dict[str, FileChange] = {}

    def add(status: str, path: str, source: str, staged: bool) -> None:
        if path in changes:
            return
        modified_at = _modified_time(repository, path)
        if not _within_day(modified_at, start, end):
            return
        changes[path] = FileChange(
            repository=repository_name,
            path=path,
            status=status,
            source=source,
            staged=staged,
            modified_at=modified_at,
        )

    for status, path in _diff_files(repository, staged=True):
        add(status, path, "staged", True)
    for status, path in _diff_files(repository, staged=False):
        add(status, path, "unstaged", False)
    untracked = _run_git(
        repository, "ls-files", "--others", "--exclude-standard", "-z"
    )
    for path in _null_entries(untracked):
        add("A", path, "untracked", False)
    return list(changes.values())


def collect_repository(
    path: Path,
    start: datetime,
    end: datetime,
) -> RepositorySnapshot:
    """收集一个 Git 仓库的当天开发证据。"""
    repository = validate_workspace(path)
    name = repository.name or str(repository)
    commits = collect_commits(repository, name, start, end)
    changes = collect_uncommitted(repository, name, start, end)
    return RepositorySnapshot(
        name=name,
        path=str(repository),
        commits=commits,
        changes=changes,
    )
