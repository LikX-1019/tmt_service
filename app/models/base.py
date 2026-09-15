"""SQLAlchemy 声明式模型基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """集中承载数据库元数据，供会话工厂和 Alembic 复用。"""
