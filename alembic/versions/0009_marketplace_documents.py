"""Documentos de respaldo de las cuidadoras y foto del perfil.

La app sincroniza cada documento que sube una cuidadora (cédula, antecedentes, certificados) y el staff
lo revisa antes de aprobar el perfil. Un documento ya aprobado que se reemplaza queda como cambio
pendiente (pending_*) sin pisar el vigente. Sigue las convenciones del esquema: tenant_id, RLS forzado,
historial inmutable y permisos por rol.

Revision ID: 0009_marketplace_documents
Revises: 0008_games
"""
import sqlalchemy as sa
from alembic import op

revision = '0009_marketplace_documents'
down_revision = '0008_games'
branch_labels = None
depends_on = None

TABLE = 'marketplace_caregiver_documents'


def _secure(table: str, history: bool = False) -> None:
    op.execute(f'ALTER TABLE admin.{table} ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE admin.{table} FORCE ROW LEVEL SECURITY')
    op.execute(f'''CREATE POLICY tenant_isolation ON admin.{table}
                   USING (tenant_id = admin.current_tenant_id())
                   WITH CHECK (tenant_id = admin.current_tenant_id())''')
    if history:  # el historial solo se lee desde la API; lo escribe el trigger
        op.execute(f'GRANT SELECT ON admin.{table} TO agecare_admin_api, agecare_admin_ro')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON admin.{table} TO agecare_admin_jobs')
    else:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON admin.{table} TO agecare_admin_api, agecare_admin_jobs')
        op.execute(f'GRANT SELECT ON admin.{table} TO agecare_admin_ro')


def upgrade():
    op.create_table(
        TABLE,
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('admin.tenants.id'), nullable=False),
        sa.Column('caregiver_id', sa.Uuid(), nullable=False),
        sa.Column('doc_type', sa.String(20), nullable=False),
        sa.Column('file_url', sa.String(500), nullable=False),
        sa.Column('original_filename', sa.String(255), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('pending_file_url', sa.String(500)),
        sa.Column('pending_filename', sa.String(255)),
        sa.Column('review_note', sa.Text()),
        sa.Column('reviewed_by', sa.Uuid(), sa.ForeignKey('admin.admin_users.id')),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['caregiver_id'], ['admin.marketplace_caregivers.caregiver_id'],
                                name='marketplace_caregiver_documents_caregiver_fkey', ondelete='CASCADE'),
        sa.UniqueConstraint('caregiver_id', 'doc_type', name='marketplace_caregiver_documents_type_uq'),
        sa.CheckConstraint("doc_type IN ('id_card', 'background_check', 'certificate')",
                           name='marketplace_caregiver_documents_type_ck'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')",
                           name='marketplace_caregiver_documents_status_ck'),
        sa.CheckConstraint("file_url ~* '^https?://'", name='marketplace_caregiver_documents_url_ck'),
        sa.CheckConstraint("pending_file_url IS NULL OR pending_file_url ~* '^https?://'",
                           name='marketplace_caregiver_documents_pending_url_ck'),
        sa.CheckConstraint("status = 'pending' OR reviewed_at IS NOT NULL OR pending_file_url IS NOT NULL",
                           name='marketplace_caregiver_documents_reviewed_ck'),
        sa.CheckConstraint('review_note IS NULL OR length(review_note) <= 1000',
                           name='marketplace_caregiver_documents_note_ck'),
        schema='admin',
        comment='Documentos de respaldo que sube la cuidadora en la app (cédula, antecedentes, '
                'certificados). El staff los revisa antes de aprobar el perfil. Historial en '
                'marketplace_caregiver_documents_history.',
    )
    op.create_index('marketplace_caregiver_documents_caregiver_idx', TABLE, ['tenant_id', 'caregiver_id'], schema='admin')
    op.execute(f'''CREATE TRIGGER marketplace_caregiver_documents_updated_at BEFORE UPDATE ON admin.{TABLE}
                   FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at()''')
    _secure(TABLE)
    op.execute(f"CALL admin.enable_history('{TABLE}')")
    _secure(f'{TABLE}_history', history=True)

    # Foto de la cuidadora: la app la sincroniza junto con el resto del perfil. El historial es una copia
    # de la tabla, así que la columna se agrega en ambas.
    op.execute('ALTER TABLE admin.marketplace_caregivers ADD COLUMN IF NOT EXISTS photo_url varchar(500)')
    op.execute('ALTER TABLE admin.marketplace_caregivers_history ADD COLUMN IF NOT EXISTS photo_url varchar(500)')


def downgrade():
    op.execute('ALTER TABLE admin.marketplace_caregivers_history DROP COLUMN IF EXISTS photo_url')
    op.execute('ALTER TABLE admin.marketplace_caregivers DROP COLUMN IF EXISTS photo_url')
    op.execute(f'DROP TABLE IF EXISTS admin.{TABLE}_history')
    op.execute(f'DROP TABLE IF EXISTS admin.{TABLE}')
