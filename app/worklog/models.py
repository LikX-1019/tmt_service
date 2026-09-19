"""工作日报的数据结构，所有总结都必须携带 Git 证据。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CommitRecord(BaseModel):
    """一条当天 Git 提交及其变更文件证据。"""

    repository: str
    sha: str
    committed_at: datetime
    message: str
    files: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    shortstat: str = ""


class FileChange(BaseModel):
    """一条当天未提交文件变更。"""

    repository: str
    path: str
    status: str
    source: str
    staged: bool
    modified_at: datetime | None = None


class WorkItem(BaseModel):
    """按功能/模块聚类后的可验证工作项。"""

    project: str
    module: str
    type: str
    title: str
    description: str
    files: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class RepositorySnapshot(BaseModel):
    """单个仓库的当日开发记录。"""

    name: str
    path: str
    commits: list[CommitRecord] = Field(default_factory=list)
    changes: list[FileChange] = Field(default_factory=list)


class WorklogCollection(BaseModel):
    """一次日报生成收集到的全部证据与工作项。"""

    date: str
    timezone: str
    start_at: datetime
    end_at: datetime
    repositories: list[RepositorySnapshot] = Field(default_factory=list)
    items: list[WorkItem] = Field(default_factory=list)

    @property
    def commit_count(self) -> int:
        """返回全仓库当天提交数量。"""
        return sum(len(repository.commits) for repository in self.repositories)

    @property
    def change_count(self) -> int:
        """返回全仓库未提交记录数量。"""
        return sum(len(repository.changes) for repository in self.repositories)
