"""Fidelización de cuidadoras: reseñas, puntos y moderación.

Una reseña de la familia al cerrar un trabajo mueve el puntaje de la cuidadora (las buenas suman, las
malas restan) y el ranking. El staff puede archivar una reseña (deja de contar, reversible) o eliminarla.
El total de puntos no se guarda: siempre es la suma del historial sin las reseñas archivadas.

Revision ID: 0010_marketplace_loyalty
Revises: 0009_marketplace_documents
"""
import sqlalchemy as sa
from alembic import op

revision = '0010_marketplace_loyalty'
down_revision = '0009_marketplace_documents'
branch_labels = None
depends_on = None

REVIEWS = 'marketplace_caregiver_reviews'
POINTS = 'marketplace_caregiver_points_log'


def _secure(table: str, history: bool = False) -> None:
    op.execute(f'ALTER TABLE admin.{table} ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE admin.{table} FORCE ROW LEVEL SECURITY')
    op.execute(f'''CREATE POLICY tenant_isolation ON admin.{table}
                   USING (tenant_id = admin.current_tenant_id())
                   WITH CHECK (tenant_id = admin.current_tenant_id())''')
    if history:
        op.execute(f'GRANT SELECT ON admin.{table} TO agecare_admin_api, agecare_admin_ro')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON admin.{table} TO agecare_admin_jobs')
    else:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON admin.{table} TO agecare_admin_api, agecare_admin_jobs')
        op.execute(f'GRANT SELECT ON admin.{table} TO agecare_admin_ro')


def upgrade():
    op.create_table(
        REVIEWS,
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('admin.tenants.id'), nullable=False),
        sa.Column('caregiver_id', sa.Uuid(), nullable=False),
        sa.Column('family_name', sa.String(120), nullable=False),
        sa.Column('rating', sa.SmallInteger(), nullable=False),
        sa.Column('comment', sa.Text()),
        sa.Column('job_reference', sa.String(120)),
        sa.Column('external_id', sa.Uuid()),
        sa.Column('archived_at', sa.DateTime(timezone=True)),
        sa.Column('archived_by', sa.Uuid(), sa.ForeignKey('admin.admin_users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['caregiver_id'], ['admin.marketplace_caregivers.caregiver_id'],
                                name='marketplace_caregiver_reviews_caregiver_fkey', ondelete='CASCADE'),
        sa.CheckConstraint('rating BETWEEN 1 AND 5', name='marketplace_caregiver_reviews_rating_ck'),
        sa.CheckConstraint('comment IS NULL OR length(comment) <= 1000', name='marketplace_caregiver_reviews_comment_ck'),
        sa.CheckConstraint('length(family_name) >= 2', name='marketplace_caregiver_reviews_family_ck'),
        sa.CheckConstraint('(archived_at IS NULL) = (archived_by IS NULL)', name='marketplace_caregiver_reviews_archived_ck'),
        schema='admin',
        comment='Reseña de una familia sobre una cuidadora al cerrar un trabajo. external_id = id de la reseña '
                'en la app (permite avisarle cuando el staff la archiva o elimina). Historial en '
                'marketplace_caregiver_reviews_history.',
    )
    op.create_index('marketplace_caregiver_reviews_caregiver_idx', REVIEWS,
                    ['tenant_id', 'caregiver_id', 'created_at'], schema='admin')
    op.create_index('marketplace_caregiver_reviews_external_idx', REVIEWS, ['tenant_id', 'external_id'],
                    unique=True, schema='admin', postgresql_where=sa.text('external_id IS NOT NULL'))
    _secure(REVIEWS)
    op.execute(f"CALL admin.enable_history('{REVIEWS}')")
    _secure(f'{REVIEWS}_history', history=True)

    op.create_table(
        POINTS,
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('admin.tenants.id'), nullable=False),
        sa.Column('caregiver_id', sa.Uuid(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(20), nullable=False),
        sa.Column('note', sa.String(300)),
        sa.Column('review_id', sa.Uuid()),
        sa.Column('awarded_by', sa.Uuid(), sa.ForeignKey('admin.admin_users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['caregiver_id'], ['admin.marketplace_caregivers.caregiver_id'],
                                name='marketplace_caregiver_points_caregiver_fkey', ondelete='CASCADE'),
        # Al eliminar una reseña se van con ella los puntos que dio o quitó.
        sa.ForeignKeyConstraint(['review_id'], [f'admin.{REVIEWS}.id'],
                                name='marketplace_caregiver_points_review_fkey', ondelete='CASCADE'),
        sa.CheckConstraint("reason IN ('job_completed', 'good_review', 'bad_review', 'manual')",
                           name='marketplace_caregiver_points_reason_ck'),
        sa.CheckConstraint('points <> 0 AND points BETWEEN -1000 AND 1000', name='marketplace_caregiver_points_amount_ck'),
        schema='admin',
        comment='Historial de puntos de fidelización. El total de una cuidadora es la suma de este historial '
                '(sin las reseñas archivadas); no se guarda aparte para que no se desincronice.',
    )
    op.create_index('marketplace_caregiver_points_caregiver_idx', POINTS, ['tenant_id', 'caregiver_id'], schema='admin')
    op.create_index('marketplace_caregiver_points_review_idx', POINTS, ['review_id'], schema='admin')
    _secure(POINTS)


def downgrade():
    op.execute(f'DROP TABLE IF EXISTS admin.{POINTS}')
    op.execute(f'DROP TABLE IF EXISTS admin.{REVIEWS}_history')
    op.execute(f'DROP TABLE IF EXISTS admin.{REVIEWS}')
