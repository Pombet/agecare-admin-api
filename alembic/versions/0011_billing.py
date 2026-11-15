"""Pasarela de pago y suscripciones (Transbank Oneclick Mall).

Tablas admin.billing_*: medios de pago (tarjeta inscrita en Oneclick, tbk_user cifrado), suscripciones
mensuales a los planes Dorado / Platino / Proveedor, cobros, reembolsos y bitácora de eventos. Sigue las
convenciones del esquema: tenant_id, RLS forzado, historial inmutable sin secretos, triggers de transición
y permisos por rol (módulos billing y billing_refunds). El DDL completo está en alembic/sql/0011_billing.sql.

Revision ID: 0011_billing
Revises: 0010_marketplace_loyalty
"""
from alembic import op

from app.migration_sql import execute_sql_snapshot

revision = '0011_billing'
down_revision = '0010_marketplace_loyalty'
branch_labels = None
depends_on = None


def upgrade():
    execute_sql_snapshot('0011_billing.sql')


def downgrade():
    # Los cobros son registros tributarios (6 años): no se borran con un downgrade si existen.
    op.execute('''DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM admin.billing_charges) THEN
                      RAISE EXCEPTION 'Hay cobros registrados: respaldar la base antes de retirar la pasarela de pago';
                    END IF;
                  END $$''')
    op.execute('DROP PROCEDURE IF EXISTS admin.billing_maintenance()')
    for table in ('billing_events', 'billing_refunds', 'billing_charges_history', 'billing_charges',
                  'billing_subscriptions_history', 'billing_subscriptions', 'billing_payment_methods_history',
                  'billing_payment_methods', 'billing_subscription_transitions'):
        op.execute(f'DROP TABLE IF EXISTS admin.{table}')
    op.execute('DROP FUNCTION IF EXISTS admin.tg_billing_subscription_transition()')
    op.execute('DROP FUNCTION IF EXISTS admin.tg_billing_charge_guard()')
    op.execute('DROP FUNCTION IF EXISTS admin.tg_billing_refund_apply()')
    op.execute("DELETE FROM admin.admin_role_permissions WHERE module IN ('billing', 'billing_refunds')")
    op.execute("DELETE FROM admin.setting_definitions WHERE key = 'billing_dunning'")
    op.drop_constraint('admin_role_permissions_module_check', 'admin_role_permissions', schema='admin', type_='check')
    op.create_check_constraint('admin_role_permissions_module_check', 'admin_role_permissions',
        "module IN ('metrics','ops','support','content','marketplace','moderation','settings','legal','staff','audit','games')",
        schema='admin')
