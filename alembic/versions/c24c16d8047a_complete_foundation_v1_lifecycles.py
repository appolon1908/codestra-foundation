"""complete Foundation v1 resource lifecycles

Revision ID: c24c16d8047a
Revises: 8d1fc3598816
Create Date: 2026-08-30 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c24c16d8047a"
down_revision: str | Sequence[str] | None = "8d1fc3598816"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("identities") as batch:
        batch.add_column(sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False))
        batch.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("ck_identity_status", "status IN ('ACTIVE','REVOKED')")
        batch.create_check_constraint("ck_identity_version", "version >= 1")
    op.execute(sa.text("UPDATE identities SET updated_at = created_at WHERE updated_at IS NULL"))
    with op.batch_alter_table("identities") as batch:
        batch.alter_column("updated_at", nullable=False)
        batch.alter_column("status", server_default=None)
        batch.alter_column("version", server_default=None)

    version_constraints = {
        "billing_accounts": "ck_billing_account_version",
        "suite_subscriptions": "ck_subscription_version",
        "invoices": "ck_invoice_version",
    }
    for table_name, constraint_name in version_constraints.items():
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
            batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
            batch.create_check_constraint(constraint_name, "version >= 1")
        op.execute(sa.text(f"UPDATE {table_name} SET updated_at = created_at WHERE updated_at IS NULL"))
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("updated_at", nullable=False)
            batch.alter_column("version", server_default=None)

    op.create_table(
        "usage_meters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("suite_code", sa.String(length=80), nullable=False),
        sa.Column("meter_code", sa.String(length=120), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("aggregation", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("aggregation IN ('SUM','MAX','PASS_THROUGH')", name="ck_meter_aggregation"),
        sa.CheckConstraint("version >= 1", name="ck_meter_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "suite_code", "meter_code", name="uq_meter_tenant_suite_code"),
    )


def downgrade() -> None:
    op.drop_table("usage_meters")
    version_constraints = {
        "invoices": "ck_invoice_version",
        "suite_subscriptions": "ck_subscription_version",
        "billing_accounts": "ck_billing_account_version",
    }
    for table_name, constraint_name in version_constraints.items():
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(constraint_name, type_="check")
            batch.drop_column("updated_at")
            batch.drop_column("version")
    with op.batch_alter_table("identities") as batch:
        batch.drop_constraint("ck_identity_version", type_="check")
        batch.drop_constraint("ck_identity_status", type_="check")
        batch.drop_column("updated_at")
        batch.drop_column("version")
        batch.drop_column("status")
