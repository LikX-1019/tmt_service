"""聊天媒体的内容寻址本地存储与安全路径解析。"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import uuid4


class UnsupportedAssetError(ValueError):
    """资源不是受支持的安全图片格式，或超过大小限制。"""


@dataclass(frozen=True, slots=True)
class StoredAsset:
    storage_key: str
    mime_type: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _detect_image(payload: bytes) -> tuple[str, str]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise UnsupportedAssetError("媒体不是允许的 PNG/JPEG/GIF/WebP 图片")


class MessageAssetStorage:
    """按 SHA-256 去重保存图片；所有读取均被限制在配置根目录内。"""

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self._root = root.resolve()
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def store_bytes(self, payload: bytes) -> StoredAsset:
        if not payload:
            raise UnsupportedAssetError("媒体内容为空")
        if len(payload) > self._max_bytes:
            raise UnsupportedAssetError("媒体超过允许的大小")
        mime_type, suffix = _detect_image(payload)
        digest = sha256(payload).hexdigest()
        storage_key = PurePosixPath(digest[:2], f"{digest}{suffix}").as_posix()
        target = self.resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(payload)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return StoredAsset(storage_key, mime_type, digest)

    def resolve(self, storage_key: str) -> Path:
        if not storage_key or PurePosixPath(storage_key).is_absolute():
            raise ValueError("媒体存储键无效")
        candidate = (self._root / Path(*PurePosixPath(storage_key).parts)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError("媒体存储键越界")
        return candidate

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)
