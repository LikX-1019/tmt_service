"""工作项聚类、中文日报渲染与受约束的 LLM 总结。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Protocol

from app.worklog.models import CommitRecord, WorkItem, WorklogCollection


class Summarizer(Protocol):
    """LLM 依赖注入协议，便于测试时不访问外部模型。"""

    def summarize(self, collection: WorklogCollection) -> str | None:
        """根据已验证证据返回日报正文；失败时返回 None。"""


_NUMBERED_LINE = re.compile(r"^(\d+)\.\s*(.+)$")
_MODULE_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("rules", ("rule", "规则"), "前置规则引擎", "规则引擎"),
    ("product", ("product", "shop", "commodity", "商品", "店铺"), "店铺运行时与商品资料", "商品与店铺"),
    ("agent", ("agent", "chat", "routing", "state", "langgraph"), "客服 Agent 路由与状态", "Agent/Service"),
    ("rag", ("rag", "qa", "knowledge", "faq", "vector"), "知识检索与问答能力", "RAG/QA"),
    ("database", ("migration", "database", "repository", "model", "alembic"), "数据库与持久化", "Database"),
    ("api", ("api", "schema", "auth", "middleware"), "API 与接口能力", "API"),
    ("console", ("web", "console", "frontend"), "客服工作台界面", "Console"),
    ("tests", ("test", "spec"), "自动化测试与稳定性", "Tests"),
    ("docs", ("docs", "readme", "doc"), "文档与工程规范", "Docs"),
)


def classify_path(path: str) -> str:
    """把文件路径映射成业务/技术模块，供证据聚类使用。"""
    normalized = path.replace("\\", "/").casefold()
    if normalized.startswith("tests/") or "/tests/" in normalized:
        return "Tests"
    if normalized.startswith("docs/") or "/docs/" in normalized:
        return "Docs"
    for _, keywords, _, label in _MODULE_RULES:
        if any(keyword in normalized for keyword in keywords):
            return label
    return normalized.split("/", 1)[0] or "Repository"


def _cluster_key(files: list[str]) -> str:
    joined = "\n".join(files).casefold()
    if files and all(classify_path(path) in {"Tests", "Docs"} for path in files):
        return "tests" if "test" in joined else "docs"
    for key, keywords, _, _ in _MODULE_RULES:
        if any(keyword in joined for keyword in keywords):
            return key
    return classify_path(files[0]) if files else "repository"


def _title(cluster_key: str, commits: list[CommitRecord]) -> str:
    """优先使用模块语义标题；纯仓库路径时回退到提交主题。"""
    mapping = {item[0]: item[2] for item in _MODULE_RULES}
    if commits and cluster_key == "repository":
        first_line = commits[0].message.splitlines()[0].strip()
        cleaned = re.sub(
            r"^(feat|fix|refactor|test|docs|chore|perf)(\([^)]*\))?:\s*",
            "",
            first_line,
        )
        return cleaned or "开发工作"
    return mapping.get(cluster_key, "开发工作")


def cluster_work_items(collection: WorklogCollection) -> list[WorkItem]:
    """按语义相近模块合并 commit、未提交文件、测试与文档变更。"""
    groups: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
        lambda: {"files": [], "commits": [], "changes": []}
    )
    primary_keys: dict[str, str] = {}
    for repository in collection.repositories:
        for commit in repository.commits:
            feature_files = [
                path
                for path in commit.files
                if classify_path(path) not in {"Tests", "Docs"}
            ]
            if feature_files:
                key = _cluster_key(feature_files)
                evidence_kind = "feature"
            else:
                key = _cluster_key(commit.files or [commit.message])
                evidence_kind = key
            if evidence_kind in {"tests", "docs"}:
                key = primary_keys.get(repository.name, key)
            else:
                primary_keys.setdefault(repository.name, key)
            group = groups[(repository.name, key)]
            if commit.sha not in group["commits"]:
                group["commits"].append(commit.sha)
            if evidence_kind in {"tests", "docs"}:
                continue
            for path in commit.files:
                if path not in group["files"]:
                    group["files"].append(path)

        for change in repository.changes:
            key = _cluster_key([change.path])
            evidence_kind = key if key in {"tests", "docs"} else "feature"
            if evidence_kind in {"tests", "docs"}:
                key = primary_keys.get(repository.name, key)
            group = groups[(repository.name, key)]
            marker = f"{change.source}:{change.path}"
            if marker not in group["changes"]:
                group["changes"].append(marker)
            if evidence_kind in {"tests", "docs"} or change.path in group["files"]:
                continue
            group["files"].append(change.path)

    items: list[WorkItem] = []
    for (project, key), group in groups.items():
        files = group["files"]
        commits = group["commits"]
        changes = group["changes"]
        modules = sorted({classify_path(path) for path in files})
        work_type = (
            "测试与稳定性"
            if key == "tests"
            else "文档与工程规范"
            if key == "docs"
            else "功能与架构"
        )
        title = _title(key, [])
        module_text = ", ".join(modules[:6]) or "仓库文件"
        description = (
            f"推进{title}相关工作，覆盖 {len(files)} 个代码/配置文件、"
            f"{len(commits)} 个提交和 {len(changes)} 项未提交记录；"
            f"涉及 {module_text}。"
        )
        evidence = [f"commit:{sha}" for sha in commits]
        evidence.extend(f"working-tree:{marker}" for marker in changes)
        items.append(
            WorkItem(
                project=project,
                module=", ".join(modules) or key,
                type=work_type,
                title=title,
                description=description,
                files=files,
                commits=commits,
                evidence=evidence,
            )
        )

    priority = {
        "rules": 0,
        "product": 0,
        "agent": 1,
        "rag": 2,
        "database": 3,
        "api": 4,
        "console": 5,
        "tests": 6,
        "docs": 7,
    }
    items.sort(
        key=lambda item: (
            priority.get(_cluster_key(item.files), 9),
            item.project,
        )
    )
    return items[:6]


def format_worklog(collection: WorklogCollection, body_items: list[str]) -> str:
    """渲染用户要求的固定中文日报格式。"""
    date = collection.start_at
    lines = [f"{date.year}/{date.month}/{date.day} 今日工作内容", ""]
    if not body_items:
        lines.append(
            "今日暂未检索到可确认的开发工作记录，请检查工作目录、"
            "Git 提交记录或未提交代码。"
        )
        return "\n".join(lines).rstrip() + "\n"
    cleaned: list[str] = []
    for item in body_items:
        value = " ".join(item.strip().split())
        if value and value not in cleaned:
            cleaned.append(value)
    rendered_count = min(len(cleaned), 6)
    for number, item in enumerate(cleaned[:rendered_count], 1):
        lines.append(f"{number}.{item}")
        if number != rendered_count:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_llm_daily_summary(value: str, expected_min: int) -> list[str] | None:
    """严格解析编号正文，拒绝标题、Markdown 列表或缺少条目的输出。"""
    if not value:
        return None
    items: list[str] = []
    for line in value.strip().splitlines():
        match = _NUMBERED_LINE.match(line.strip())
        if not match:
            continue
        text = " ".join(match.group(2).split())
        if text and text not in items:
            items.append(text)
    if len(items) < expected_min or len(items) > 6:
        return None
    return items


class EvidenceConstrainedSummarizer:
    """复用项目统一 LLM Factory，并禁止模型脱离证据发挥。"""

    def summarize(self, collection: WorklogCollection) -> str | None:
        """把预处理后的 Git 证据交给统一 LLM，只返回严格格式正文。"""
        # 延迟导入，使确定性运行、单元测试和未配置 LLM 的环境无需初始化模型。
        from langchain_core.messages import HumanMessage

        from app.core.config import get_settings
        from app.factories.llm_factory import get_llm

        settings = get_settings()
        if settings.llm_api_key is None:
            return None
        payload = {
            "date": collection.date,
            "repositories": [
                {
                    "name": repository.name,
                    "commits": [
                        commit.model_dump(mode="json", exclude={"repository"})
                        for commit in repository.commits
                    ],
                    "uncommitted": [
                        change.model_dump(mode="json", exclude={"repository"})
                        for change in repository.changes
                    ],
                }
                for repository in collection.repositories
            ],
            "preclustered_items": [
                item.model_dump(mode="json") for item in collection.items
            ],
        }
        prompt = """你是资深研发负责人。根据 JSON 中的 Git 证据，把当天工作归纳成中文日报。
要求：
1. 只使用证据，禁止编造功能、测试结果或业务效果。
2. 按重要性合并同类工作，优先 4 到 5 条；证据不足时可以更少。
3. 每条 60 到 150 个中文字，说明做了什么、核心技术、关键能力和结果。
4. 不要罗列文件名流水账，不要 Markdown 列表，不要额外解释。
5. 只输出以下格式：
1.xxx

2.xxx

JSON 证据：
""".rstrip()
        prompt += "\n" + json.dumps(payload, ensure_ascii=False)
        model = get_llm("summarize", temperature=0.1)
        response = model.invoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = "".join(
                part if isinstance(part, str) else str(part) for part in content
            )
        return content
