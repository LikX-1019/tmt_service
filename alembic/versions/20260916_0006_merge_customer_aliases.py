"""合并拼多多列表状态后缀造成的重复顾客和会话。"""

import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260916_0006"
down_revision: Union[str, None] = "20260916_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical(value: str) -> str:
    match = re.match(r"^(\d+)(?:-|$)", value)
    return match.group(1) if match else re.sub(r"-(?:all|reply)$", "", value)


def _move_messages(
    bind: sa.engine.Connection,
    *,
    source_conversation_id: str,
    target_conversation_id: str,
    target_customer_id: str,
) -> None:
    messages = list(
        bind.execute(
            sa.text(
                "SELECT id, platform_message_id FROM messages "
                "WHERE conversation_id=:conversation_id"
            ),
            {"conversation_id": source_conversation_id},
        ).mappings()
    )
    for message in messages:
        existing = None
        if message["platform_message_id"]:
            existing = bind.execute(
                sa.text(
                    "SELECT id FROM messages WHERE conversation_id=:conversation_id "
                    "AND platform_message_id=:platform_message_id LIMIT 1"
                ),
                {
                    "conversation_id": target_conversation_id,
                    "platform_message_id": message["platform_message_id"],
                },
            ).first()
        if existing:
            bind.execute(
                sa.text("DELETE FROM messages WHERE id=:id"), {"id": message["id"]}
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE messages SET conversation_id=:conversation_id, "
                    "customer_id=:customer_id WHERE id=:id"
                ),
                {
                    "conversation_id": target_conversation_id,
                    "customer_id": target_customer_id,
                    "id": message["id"],
                },
            )


def _merge_conversation(
    bind: sa.engine.Connection,
    *,
    source_id: str,
    target_id: str,
    target_customer_id: str,
) -> None:
    _move_messages(
        bind,
        source_conversation_id=source_id,
        target_conversation_id=target_id,
        target_customer_id=target_customer_id,
    )
    for table in ("outbound_jobs", "reply_decisions"):
        bind.execute(
            sa.text(
                f"UPDATE {table} SET conversation_id=:target_id "
                "WHERE conversation_id=:source_id"
            ),
            {"target_id": target_id, "source_id": source_id},
        )
    bind.execute(sa.text("DELETE FROM conversations WHERE id=:id"), {"id": source_id})


def upgrade() -> None:
    bind = op.get_bind()
    customers = list(
        bind.execute(
            sa.text(
                "SELECT id, shop_id, platform_customer_id FROM customers "
                "ORDER BY first_seen_at, id"
            )
        ).mappings()
    )
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in customers:
        raw = str(row["platform_customer_id"])
        canonical = _canonical(raw)
        if canonical.isdigit():
            groups.setdefault((str(row["shop_id"]), canonical), []).append(dict(row))

    for (shop_id, canonical), rows in groups.items():
        if len(rows) < 2:
            continue
        primary = next(
            (row for row in rows if row["platform_customer_id"] == canonical), rows[0]
        )
        primary_customer_id = str(primary["id"])
        primary_conversation = bind.execute(
            sa.text(
                "SELECT id FROM conversations WHERE shop_id=:shop_id "
                "AND customer_id=:customer_id ORDER BY created_at, id LIMIT 1"
            ),
            {"shop_id": shop_id, "customer_id": primary_customer_id},
        ).mappings().first()

        for duplicate in rows:
            duplicate_customer_id = str(duplicate["id"])
            if duplicate_customer_id == primary_customer_id:
                continue
            duplicate_conversations = list(
                bind.execute(
                    sa.text(
                        "SELECT id FROM conversations WHERE shop_id=:shop_id "
                        "AND customer_id=:customer_id ORDER BY created_at, id"
                    ),
                    {"shop_id": shop_id, "customer_id": duplicate_customer_id},
                ).mappings()
            )
            for duplicate_conversation in duplicate_conversations:
                duplicate_conversation_id = str(duplicate_conversation["id"])
                if primary_conversation is None:
                    bind.execute(
                        sa.text(
                            "UPDATE conversations SET customer_id=:customer_id "
                            "WHERE id=:id"
                        ),
                        {
                            "customer_id": primary_customer_id,
                            "id": duplicate_conversation_id,
                        },
                    )
                    primary_conversation = {"id": duplicate_conversation_id}
                else:
                    _merge_conversation(
                        bind,
                        source_id=duplicate_conversation_id,
                        target_id=str(primary_conversation["id"]),
                        target_customer_id=primary_customer_id,
                    )
            bind.execute(
                sa.text(
                    "UPDATE messages SET customer_id=:target_id "
                    "WHERE customer_id=:source_id"
                ),
                {
                    "target_id": primary_customer_id,
                    "source_id": duplicate_customer_id,
                },
            )
            bind.execute(
                sa.text("DELETE FROM customers WHERE id=:id"),
                {"id": duplicate_customer_id},
            )

        bind.execute(
            sa.text(
                "UPDATE customers SET platform_customer_id=:platform_id WHERE id=:id"
            ),
            {"platform_id": canonical, "id": primary_customer_id},
        )
        bind.execute(
            sa.text(
                "UPDATE conversations SET platform_conversation_id=:platform_id "
                "WHERE shop_id=:shop_id AND customer_id=:customer_id"
            ),
            {
                "platform_id": canonical,
                "shop_id": shop_id,
                "customer_id": primary_customer_id,
            },
        )


def downgrade() -> None:
    # 身份合并不可逆；降级不会重新制造重复顾客。
    pass
