from datetime import datetime, timezone

import pytest

from app.integrations.pdd.base import normalize_platform_customer_id
from app.integrations.pdd.parser import build_fingerprint, parse_browser_message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1855811761-0-all", "1855811761"),
        ("1855811761-0-reply", "1855811761"),
        ("1855811761-0-unTimeout", "1855811761"),
        ("buyer-1-reply", "buyer-1"),
    ],
)
def test_platform_customer_id_normalizes_list_state_suffixes(
    raw: str, expected: str
) -> None:
    assert normalize_platform_customer_id(raw) == expected


def test_platform_message_id_is_primary_deduplication_key() -> None:
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    first = build_fingerprint(
        conversation_id="c1",
        direction="inbound",
        kind="text",
        content="第一份内容",
        occurred_at=now,
        platform_message_id="m1",
    )
    second = build_fingerprint(
        conversation_id="c1",
        direction="inbound",
        kind="text",
        content="变更的内容",
        occurred_at=now,
        platform_message_id="m1",
    )
    assert first == second


def test_fallback_fingerprint_ignores_unstable_dom_position() -> None:
    now = datetime(2026, 9, 15, 8, 0, 3, tzinfo=timezone.utc)
    kwargs = {
        "conversation_id": "c1",
        "direction": "inbound",
        "kind": "text",
        "content": "重力珠在哪？",
        "occurred_at": now,
        "platform_message_id": None,
    }
    assert build_fingerprint(**kwargs, dom_key="2") == build_fingerprint(
        **kwargs, dom_key="9"
    )


def test_parser_marks_unknown_media_as_unsupported() -> None:
    message = parse_browser_message(
        {
            "conversation_id": "buyer-1",
            "display_name": "测试顾客",
            "direction": "unexpected",
            "kind": "voice",
            "content": "12 秒语音",
            "message_id": "m2",
        },
        is_backfill=True,
    )
    assert message.direction == "inbound"
    assert message.kind == "unsupported"
    assert message.is_backfill is True


def test_parser_preserves_customer_avatar_url() -> None:
    message = parse_browser_message(
        {
            "conversation_id": "buyer-1",
            "display_name": "顾客甲",
            "avatar_url": "https://img.example/avatar.jpg?token=secret",
            "content": "你好",
            "message_id": "avatar-m1",
        }
    )
    assert message.display_name == "顾客甲"
    assert message.avatar_url == "https://img.example/avatar.jpg?token=secret"


def test_parser_rejects_message_without_conversation() -> None:
    with pytest.raises(ValueError, match="conversation_id"):
        parse_browser_message({"content": "hello"})


def test_parser_rejects_message_from_another_customer_panel() -> None:
    with pytest.raises(ValueError, match="归属"):
        parse_browser_message(
            {
                "conversation_id": "buyer-1",
                "platform_customer_id": "buyer-2",
                "content": "不应串线",
            }
        )


@pytest.mark.parametrize(
    ("page_time", "expected"),
    [
        ("14:30", datetime(2026, 9, 15, 6, 30, tzinfo=timezone.utc)),
        ("昨天 23:05", datetime(2026, 9, 14, 15, 5, tzinfo=timezone.utc)),
        ("2026年08月14日 11:25:07", datetime(2026, 8, 14, 3, 25, 7, tzinfo=timezone.utc)),
    ],
)
def test_parser_understands_pdd_chinese_page_times(
    page_time: str, expected: datetime
) -> None:
    message = parse_browser_message(
        {
            "conversation_id": "buyer-1",
            "content": "测试",
            "message_id": f"m-{page_time}",
            "timestamp": page_time,
        },
        observed_at=datetime(2026, 9, 15, 8, tzinfo=timezone.utc),
    )
    assert message.occurred_at == expected
