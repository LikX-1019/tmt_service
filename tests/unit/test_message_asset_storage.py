from pathlib import Path

import pytest

from app.services.message_asset_storage import MessageAssetStorage, UnsupportedAssetError


def test_store_bytes_is_content_addressed_and_rejects_unknown_format(tmp_path: Path) -> None:
    storage = MessageAssetStorage(tmp_path / "assets", max_bytes=1024)
    payload = b"\x89PNG\r\n\x1a\n" + b"payload"
    first = storage.store_bytes(payload)
    second = storage.store_bytes(payload)
    assert first == second
    assert storage.resolve(first.storage_key).read_bytes() == payload
    with pytest.raises(UnsupportedAssetError):
        storage.store_bytes(b"not-an-image")


def test_storage_key_cannot_escape_root(tmp_path: Path) -> None:
    storage = MessageAssetStorage(tmp_path / "assets", max_bytes=1024)
    with pytest.raises(ValueError):
        storage.resolve("../outside.png")
