"""office hours on employees, and alerts when they are closed early

Revision ID: 3d9f2a6b1c07
Revises: 7b1e5d0c9a42
Created: 2026-11-28 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3d9f2a6b1c07"
down_revision = "7b1e5d0c9a42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("work_days", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("work_start", sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column("work_minutes", sa.Integer(), nullable=True))

    op.create_table(
        "focus_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restart_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=op.f("fk_focus_alerts_organization_id_organizations"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_focus_alerts_user_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"],
                                name=op.f("fk_focus_alerts_device_id_devices"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_focus_alerts")),
    )
    with op.batch_alter_table("focus_alerts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_focus_alerts_organization_id"),
                              ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_focus_alerts_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_focus_alerts_device_id"), ["device_id"], unique=False)


def downgrade() -> None:
    op.drop_table("focus_alerts")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("work_minutes")
        batch_op.drop_column("work_start")
        batch_op.drop_column("work_days")
