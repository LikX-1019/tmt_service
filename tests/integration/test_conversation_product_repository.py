import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.chat import ChatConversationProduct
from app.repositories.conversation_repository import ConversationProductRepository


@pytest_asyncio.fixture
async def repository():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(ChatConversationProduct.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield ConversationProductRepository(factory)
    await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_product_binding_persists_and_switches(repository) -> None:
    await repository.bind_product("c1", product_id="1001", product_name="护腕")
    binding = await repository.get_binding("c1")
    assert (binding.product_id, binding.product_name) == ("1001", "护腕")

    await repository.bind_product("c1", product_id="2002", product_name="护膝")
    binding = await repository.get_binding("c1")
    assert (binding.product_id, binding.product_name) == ("2002", "护膝")


@pytest.mark.asyncio
async def test_conversation_product_binding_can_be_cleared(repository) -> None:
    await repository.bind_product("c1", product_id="1001", product_name="护腕")
    await repository.clear_binding("c1")
    assert await repository.get_binding("c1") is None


def _history(product_id: str, product_name: str) -> dict[str, str]:
    return {"product_id": product_id, "product_name": product_name}


@pytest.mark.asyncio
async def test_conversation_product_history_persists_recent_products(repository) -> None:
    await repository.bind_product(
        "c1", product_id="1001", product_name="护腕", recent_products=[]
    )
    binding = await repository.get_binding("c1")
    assert binding.recent_products == []

    await repository.bind_product(
        "c1",
        product_id="2002",
        product_name="护膝",
        recent_products=[_history("1001", "护腕")],
    )
    binding = await repository.get_binding("c1")

    assert (binding.product_id, binding.product_name) == ("2002", "护膝")
    assert binding.recent_products == [_history("1001", "护腕")]

    await repository.bind_product(
        "c1",
        product_id="1001",
        product_name="护腕",
        recent_products=[
            _history("2002", "护膝"),
            _history("1001", "护腕"),
        ],
    )
    binding = await repository.get_binding("c1")
    assert binding.recent_products == [
        _history("2002", "护膝"),
        _history("1001", "护腕"),
    ]
