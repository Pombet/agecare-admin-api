"""Motor async de SQLAlchemy y sesión por petición."""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy import event

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "before_flush")
def _assign_tenant_to_new_rows(session: Session, _flush_context, _instances) -> None:
    """Fill tenant_id for writes made inside a tenant-scoped request/session."""
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        return
    for instance in session.new:
        if hasattr(type(instance), "tenant_id") and getattr(instance, "tenant_id", None) is None:
            instance.tenant_id = tenant_id


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: una sesión por petición, commit al éxito."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
