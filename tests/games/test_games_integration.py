"""Pruebas aisladas de juegos: no usan seed, .env ni bases existentes.

Ejecutar con --confcutdir=tests/games para excluir las fixtures del seed legado.
La autenticación se sustituye por una identidad de prueba; require() es real.
PostgreSQL/RLS se verifican por separado, no mediante SQLite.
"""
import importlib.util
from io import StringIO
from html.parser import HTMLParser
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from urllib.parse import urlsplit

os.environ['ADMIN_DATABASE_URL'] = 'sqlite+aiosqlite://'
os.environ['ADMIN_GAME_PUBLIC_BASE_URL'] = ''

import pytest
import pytest_asyncio
from fastapi import Depends, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import database
from app.deps import get_current_admin
from app.errors import unauthorized
from app.main import app
from app.models import AuditLog, GameApp

ROOT = Path(__file__).resolve().parents[2]
TENANT_A, TENANT_B = UUID(int=1), UUID(int=2)


@pytest_asyncio.fixture
async def environment(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite://', poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, 'connect')
    def attach(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("ATTACH DATABASE ':memory:' AS admin")
        cursor.close()
        dbapi_connection.create_function('now', 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_connection.create_function('gen_random_uuid', 0, lambda: uuid4().hex)

    async with engine.begin() as connection:
        await connection.run_sync(GameApp.__table__.create)
        # Las tablas canónicas tienen tipos PostgreSQL; solo el audit mínimo
        # se reproduce aquí para ejercitar el audit() existente de verdad.
        await connection.exec_driver_sql('''CREATE TABLE admin.audit_log (
            id CHAR(32) DEFAULT (gen_random_uuid()), tenant_id CHAR(32) NOT NULL,
            action VARCHAR(60) NOT NULL, created_at TEXT DEFAULT (now()),
            actor_id CHAR(32), actor_name TEXT, actor_role TEXT, entity_type TEXT,
            entity_id CHAR(32), before JSON, after JSON, ip TEXT, user_agent TEXT,
            request_id CHAR(32), PRIMARY KEY (id, created_at))''')

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, '_engine', engine)
    monkeypatch.setattr(database, '_session_factory', factory)

    async def db_override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def identity(request: Request, db=Depends(database.get_db)):
        role = request.headers.get('x-test-role')
        if role is None:
            raise unauthorized()
        tenant = UUID(request.headers.get('x-test-tenant', str(TENANT_A)))
        admin = SimpleNamespace(id=UUID(int=10), full_name='Cuenta de prueba',
                                role=role, tenant_id=tenant)
        db.info['tenant_id'] = tenant
        request.state.actor = admin
        return admin

    app.dependency_overrides[database.get_db] = db_override
    app.dependency_overrides[get_current_admin] = identity
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


def headers(role='editor', tenant=TENANT_A):
    return {'x-test-role': role, 'x-test-tenant': str(tenant)}


def body(slug='memorama'):
    return dict(title='Juego de prueba', slug=slug, category='Memoria',
                target_url=f'/static/juegos/{slug}/index.html')


@pytest.mark.parametrize('role', ['admin', 'editor', 'analyst', 'support', 'moderator'])
async def test_permissions(environment, role):
    client, factory = environment
    assert (await client.get('/api/v1/admin/games-apps/', headers=headers(role))).status_code == 200
    created = await client.post('/api/v1/admin/games-apps/', headers=headers(role), json=body())
    assert created.status_code == (201 if role in ('admin', 'editor') else 403)
    if role not in ('admin', 'editor'):
        async with factory() as db:
            assert (await db.scalars(select(GameApp))).all() == []
            assert (await db.scalars(select(AuditLog))).all() == []


async def test_lifecycle_and_iframe(environment):
    client, factory = environment
    created = await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body())
    assert created.status_code == 201, created.text
    item = created.json()
    url = item['target_url']
    assert f'/static/{TENANT_A}/juegos/memorama/index.html' in url
    assert item['resource_kind'] == 'local' and item['is_active'] is False
    assert (await client.get(url)).status_code == 403
    assert (await client.post(f"/api/v1/admin/games-apps/{item['id']}/toggle", headers=headers())).status_code == 200
    loaded = await client.get(url)
    assert loaded.status_code == 200
    assert 'disponibilidad.js' in loaded.text and 'Cache-Control' in loaded.headers
    head = await client.head(url, headers={'Origin': 'null'})
    assert head.status_code == 200 and head.headers['access-control-allow-origin'] == '*'
    assert (await client.get(f'/static/{TENANT_A}/juegos/compartido/css/accesibilidad.css')).status_code == 200
    listed = await client.get('/api/v1/admin/games-apps/active', params={'tenant_id': str(TENANT_A)})
    assert [g['id'] for g in listed.json()] == [item['id']]
    edited = await client.put(f"/api/v1/admin/games-apps/{item['id']}", headers=headers(), json={'title': 'Memorama editado', 'target_url': url})
    assert edited.status_code == 200, edited.text
    assert edited.json()['title'] == 'Memorama editado'
    await client.post(f"/api/v1/admin/games-apps/{item['id']}/toggle", headers=headers())
    blocked = await client.head(url, headers={'Origin': 'null'})
    assert blocked.status_code == 403 and blocked.headers['access-control-allow-origin'] == '*'
    assert (await client.get('/static/juegos/memorama/index.html')).status_code == 403
    assert (await client.delete(f"/api/v1/admin/games-apps/{item['id']}", headers=headers())).status_code == 204
    assert (await client.get(url)).status_code == 403
    async with factory() as db:
        logs = (await db.scalars(select(AuditLog))).all()
        assert {e.action for e in logs} == {'games.create', 'games.activate', 'games.update', 'games.deactivate', 'games.delete'}
        assert all(e.tenant_id == TENANT_A and e.actor_name == 'Cuenta de prueba' for e in logs)


async def test_tenant_isolation_even_without_rls(environment):
    client, _ = environment
    item = (await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body())).json()
    await client.post(f"/api/v1/admin/games-apps/{item['id']}/toggle", headers=headers())
    assert (await client.get('/api/v1/admin/games-apps/', headers=headers(tenant=TENANT_B))).json() == []
    for method, suffix, data in [('PUT', '', {'title': 'Cambio ajeno'}), ('POST', '/toggle', None), ('DELETE', '', None)]:
        result = await client.request(method, f"/api/v1/admin/games-apps/{item['id']}{suffix}", headers=headers(tenant=TENANT_B), json=data)
        assert result.status_code == 404
    assert (await client.get('/api/v1/admin/games-apps/active', params={'tenant_id': str(TENANT_B)})).json() == []
    assert (await client.get(item['target_url'].replace(str(TENANT_A), str(TENANT_B)))).status_code == 403
    other = await client.post('/api/v1/admin/games-apps/', headers=headers(tenant=TENANT_B), json=body())
    assert other.status_code == 201  # El mismo slug pertenece a otro catálogo.


async def test_invalid_urls_and_anonymous_access(environment):
    client, _ = environment
    assert (await client.get('/api/v1/admin/games-apps/')).status_code == 401
    assert (await client.post('/api/v1/admin/games-apps/', json=body())).status_code == 401
    assert (await client.get('/api/v1/admin/games-apps/active')).status_code == 422
    for url in ['/static/juegos/inexistente/index.html', 'javascript:alert(1)', 'http://externo.example/juego', '/static/juegos/memorama/index.html?x=1', f'/static/{TENANT_B}/juegos/memorama/index.html']:
        result = await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body() | {'target_url': url})
        assert result.status_code == 422, (url, result.text)
    external = await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body('externo') | {'target_url': 'https://example.org/juego'})
    assert external.status_code == 201 and external.json()['resource_kind'] == 'external'


async def test_rejected_writes_and_audit_rollback(environment, monkeypatch):
    client, factory = environment
    item = (await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body())).json()
    duplicate = await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body())
    assert duplicate.status_code == 400
    assert (await client.put(f"/api/v1/admin/games-apps/{item['id']}", headers=headers(), json={'is_active': True})).status_code == 422
    for role in ('analyst', 'support', 'moderator'):
        for method, suffix, data in [('PUT', '', {'title': 'Cambio rechazado'}), ('POST', '/toggle', None), ('DELETE', '', None)]:
            assert (await client.request(method, f"/api/v1/admin/games-apps/{item['id']}{suffix}", headers=headers(role), json=data)).status_code == 403
    from app.routers import games_apps
    async def failed_audit(*args, **kwargs):
        raise RuntimeError('Fallo simulado de auditoría')
    monkeypatch.setattr(games_apps, 'audit', failed_audit)
    with pytest.raises(RuntimeError, match='Fallo simulado'):
        await client.put(f"/api/v1/admin/games-apps/{item['id']}", headers=headers(), json={'title': 'Cambio sin auditoría'})
    async with factory() as db:
        assert (await db.get(GameApp, UUID(item['id']))).title == 'Juego de prueba'
        assert len((await db.scalars(select(AuditLog))).all()) == 1


def test_catalog_and_immutable_migrations():
    spec = importlib.util.spec_from_file_location('games_revision', ROOT / 'alembic/versions/0008_games.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    entries = {p.parent.name for p in (ROOT / 'app/static/juegos').glob('*/index.html')}
    assert len(entries) == 30
    assert len(migration.INITIAL_GAMES) == 30
    assert {g['slug'] for g in migration.INITIAL_GAMES} == entries
    for slug in entries:
        assert (ROOT / f'app/static/juegos/{slug}/juego.json').is_file()


def test_local_styles_and_scripts_exist_for_every_game():
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.refs = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            key = 'src' if tag == 'script' else 'href' if tag == 'link' else None
            if key and attrs.get(key):
                self.refs.append(attrs[key])

    for entry in (ROOT / 'app/static/juegos').glob('*/index.html'):
        parser = Assets()
        parser.feed(entry.read_text(encoding='utf-8'))
        assert parser.refs, entry
        for ref in parser.refs:
            url = urlsplit(ref)
            if not url.netloc and not url.scheme:
                assert not url.path.startswith('/'), (entry, ref)
                assert (entry.parent / url.path).is_file(), (entry, ref)


def test_postgresql_migration_sql_without_database():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('games_revision_sql', ROOT / 'alembic/versions/0008_games.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={
        'as_sql': True, 'literal_binds': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert 'CREATE TABLE admin.games_apps' in sql
    assert 'FORCE ROW LEVEL SECURITY' in sql
    assert 'WITH CHECK (tenant_id = admin.current_tenant_id())' in sql
    assert 'INSERT INTO admin.games_apps' in sql
    assert sql.count('DROP TABLE') == 0
    assert 'UPDATE admin.admin_users' not in sql
    assert all(g['slug'] in sql for g in migration.INITIAL_GAMES)




async def test_all_local_games_can_be_served(environment):
    client, _ = environment
    for entry in (ROOT / 'app/static/juegos').glob('*/index.html'):
        slug = entry.parent.name
        result = await client.post('/api/v1/admin/games-apps/', headers=headers(), json=body(slug))
        assert result.status_code == 201, slug
        item = result.json()
        assert (await client.post(f"/api/v1/admin/games-apps/{item['id']}/toggle", headers=headers())).status_code == 200
        loaded = await client.get(item['target_url'])
        assert loaded.status_code == 200 and 'disponibilidad.js' in loaded.text, slug
