"""Agent 检查点存储协议与单机原子文件实现。"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from app.agent.state import AgentState, WorkflowStatus, utcnow
from app.core.config import PROJECT_ROOT


DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "data" / "runtime" / "agent-checkpoints"
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class CheckpointError(RuntimeError):
    """检查点读写失败的基类。"""


class CheckpointConflictError(CheckpointError):
    """检查点版本落后，拒绝覆盖较新的流程状态。"""


class CheckpointCorruptedError(CheckpointError):
    """检查点内容损坏或无法通过状态模型校验。"""


class CheckpointStore(Protocol):
    """检查点后端协议，后续可替换为 MySQL 或 Redis 实现。"""

    async def save(self, state: AgentState, *, reason: str) -> AgentState:
        """以乐观版本约束保存状态。"""

    async def load(self, run_id: str) -> AgentState | None:
        """读取指定流程最近一次检查点。"""

    async def delete(self, run_id: str) -> None:
        """删除无需继续保留的检查点。"""


class FileCheckpointStore:
    """适配当前单机部署的原子 JSON 检查点存储。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or DEFAULT_CHECKPOINT_DIR
        self._locks: dict[str, asyncio.Lock] = {}

    async def save(self, state: AgentState, *, reason: str) -> AgentState:
        """先比较 revision，再用同目录原子替换防止半写文件。"""
        lock = self._locks.setdefault(state.run_id, asyncio.Lock())
        async with lock:
            current = await self.load(state.run_id)
            if current is not None and current.revision != state.revision:
                raise CheckpointConflictError(
                    f"检查点版本冲突：当前 {current.revision}，提交 {state.revision}"
                )
            persisted = state.model_copy(deep=True)
            persisted.revision += 1
            persisted.updated_at = utcnow()
            persisted.checkpoint_reason = reason
            persisted.sync_contract_state()
            await asyncio.to_thread(self._write_atomic, persisted)
            state.revision = persisted.revision
            state.updated_at = persisted.updated_at
            state.checkpoint_reason = persisted.checkpoint_reason
            state.sync_contract_state()
            return state

    async def load(self, run_id: str) -> AgentState | None:
        """读取并验证完整状态；损坏文件不会被静默当作无检查点。"""
        path = self._path_for(run_id)
        if not path.exists():
            return None
        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return AgentState.model_validate_json(content)
        except Exception as exc:
            raise CheckpointCorruptedError(f"检查点无法读取：{run_id}") from exc

    async def delete(self, run_id: str) -> None:
        """删除指定检查点，文件不存在时保持幂等。"""
        path = self._path_for(run_id)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def list_recoverable(self) -> list[AgentState]:
        """列出尚未完成的流程，供进程启动时恢复调度。"""
        if not self._directory.exists():
            return []
        states: list[AgentState] = []
        for path in sorted(self._directory.glob("*.json")):
            state = await self.load(path.stem)
            if state and state.status not in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.CANCELLED,
            }:
                states.append(state)
        return states

    def _path_for(self, run_id: str) -> Path:
        if not SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id 只能包含字母、数字、点、下划线和连字符")
        return self._directory / f"{run_id}.json"

    def _write_atomic(self, state: AgentState) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{state.run_id}-",
            suffix=".tmp",
            dir=self._directory,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(state.model_dump_json())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path_for(state.run_id))
        finally:
            temporary_path.unlink(missing_ok=True)
