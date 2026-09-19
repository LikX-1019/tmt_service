import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.chat import ChatMessage
from app.repositories.chat_message_repository import ChatConversationMessageRepository


@pytest_asyncio.fixture
async def repository():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(ChatMessage.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield ChatConversationMessageRepository(factory)
    await engine.dispose()


@pytest.mark.asyncio
async def test_recent_turns_are_ordered_and_limited(repository) -> None:
    for number in range(1, 13):
        await repository.append_customer_message("c1", f"问题 {number}")
        await repository.append_assistant_message("c1", f"答案 {number}")

    turns = await repository.list_recent_turns("c1", limit=3)

    assert turns == [
        {"customer": "问题 10", "assistant": "答案 10"},
        {"customer": "问题 11", "assistant": "答案 11"},
        {"customer": "问题 12", "assistant": "答案 12"},
    ]


@pytest.mark.asyncio
async def test_pending_customer_message_is_included(repository) -> None:
    await repository.append_customer_message("c1", "旧问题")
    await repository.append_assistant_message("c1", "旧答案")
    await repository.append_customer_message("c1", "当前问题")

    turns = await repository.list_recent_turns("c1")

    assert turns == [
        {"customer": "旧问题", "assistant": "旧答案"},
        {"customer": "当前问题", "assistant": None},
    ]
