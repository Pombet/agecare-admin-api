# Canonical PostgreSQL migration snapshots

These six SQL snapshots are derived from `agecare_admin_ddl.sql` in the parent
project folder. They form the additive `admin` schema migration chain after the
prototype revision `0001`; the prototype tables are left intact while the API
is migrated to the canonical schema.

The revision-to-source mapping is:

| Revision | DDL sections |
| --- | --- |
| `0002_core` | 0-3: extensions, roles, common functions, tenants and catalogs |
| `0003_staff_audit` | 4: staff, sessions and audit |
| `0004_metrics` | 5-6: commercial metrics, profiles and feature adoption |
| `0005_ops_support` | 7-8: operational status, incidents and support |
| `0006_business_modules` | 9-12: content, marketplace, moderation, settings, legal and jobs |
| `0007_security_retention` | 13-17: entity history, RLS, grants, partitions and catalog seeds |

Once deployed, do not edit these SQL snapshots. Future schema changes belong in
new Alembic revisions. These baseline revisions intentionally have no automatic
downgrade because dropping the canonical schema would destroy data; restore a
database backup for recovery.
