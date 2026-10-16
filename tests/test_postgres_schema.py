"""Garantías del esquema real: RLS, historial, constraints y auditoría."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.asyncio


async def tenant_products(connection):
    tenant_a = await connection.scalar(text("SELECT id FROM admin.tenants WHERE code = 'local'"))
    actor = await connection.scalar(text(
        "SELECT id FROM admin.admin_users WHERE tenant_id = :tid LIMIT 1"), {"tid": tenant_a})
    tenant_b = uuid4()
    await connection.execute(text(
        "INSERT INTO admin.tenants (id, code, name, country_code, currency_code, timezone, environment) "
        "VALUES (:id, :code, 'Tenant B', 'CL', 'CLP', 'America/Santiago', 'development')"),
        {"id": tenant_b, "code": f"test-{tenant_b.hex[:12]}"})
    products = []
    for tenant in (tenant_a, tenant_b):
        product = uuid4()
        await connection.execute(text(
            "INSERT INTO admin.marketplace_products "
            "(id, tenant_id, name, category, vendor, external_url, created_by) "
            "VALUES (:id, :tid, 'Producto aislado', 'Apoyo', 'AgeCare', 'https://example.com', :actor)"),
            {"id": product, "tid": tenant, "actor": actor})
        products.append(product)
    return tenant_a, tenant_b, actor, products


@pytest.mark.parametrize("role", ["agecare_admin_api", "agecare_admin_ro"])
async def test_rls_reads_only_current_tenant(db_connection, role):
    tenant_a, tenant_b, _, products = await tenant_products(db_connection)
    # SET ROLE es imprescindible: el propietario de las tablas omite RLS.
    await db_connection.execute(text(f"SET LOCAL ROLE {role}"))
    await db_connection.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    assert await db_connection.scalar(text("SELECT count(*) FROM admin.marketplace_products")) == 0
    for tenant, own, other in ((tenant_a, products[0], products[1]),
                               (tenant_b, products[1], products[0])):
        await db_connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                                    {"tid": str(tenant)})
        visible = set((await db_connection.execute(text(
            "SELECT id FROM admin.marketplace_products"))).scalars())
        assert own in visible
        assert other not in visible


async def test_rls_rejects_cross_tenant_insert(db_connection):
    tenant_a, tenant_b, actor, _ = await tenant_products(db_connection)
    await db_connection.execute(text("SET LOCAL ROLE agecare_admin_api"))
    await db_connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                                {"tid": str(tenant_a)})
    with pytest.raises(DBAPIError) as error:
        async with db_connection.begin_nested():
            await db_connection.execute(text(
                "INSERT INTO admin.marketplace_products "
                "(tenant_id, name, category, vendor, external_url, created_by) "
                "VALUES (:tid, 'No permitido', 'Apoyo', 'AgeCare', 'https://example.com', :actor)"),
                {"tid": tenant_b, "actor": actor})
    assert error.value.orig.sqlstate == "42501"


async def test_product_changes_create_history(db_connection):
    _, _, _, products = await tenant_products(db_connection)
    await db_connection.execute(text(
        "UPDATE admin.marketplace_products SET name = 'Producto actualizado' WHERE id = :id"),
        {"id": products[0]})
    history = (await db_connection.execute(text(
        "SELECT op, name FROM admin.marketplace_products_history WHERE id = :id ORDER BY history_id"),
        {"id": products[0]})).all()
    assert history == [("I", "Producto aislado"), ("U", "Producto actualizado")]


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_audit_is_immutable_even_for_owner(client, admin_headers, db_connection, operation):
    # El login proporciona una fila real, evitando probar una tabla vacía.
    audit_id = await db_connection.scalar(text(
        "SELECT id FROM admin.audit_log WHERE action = 'auth.login' LIMIT 1"))
    assert audit_id is not None
    statement = ("UPDATE admin.audit_log SET action = 'tests.tampered' WHERE id = :id"
                 if operation == "UPDATE" else "DELETE FROM admin.audit_log WHERE id = :id")
    with pytest.raises(DBAPIError) as error:
        async with db_connection.begin_nested():
            await db_connection.execute(text(statement), {"id": audit_id})
    assert error.value.orig.sqlstate == "42501"
