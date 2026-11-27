"""task weekly schedule (employee computers start the task by themselves)

Revision ID: 7b1e5d0c9a42
Revises: 4c12d84c2a8e
Created: 2026-11-27 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b1e5d0c9a42"
down_revision = "4c12d84c2a8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("schedule_days", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("schedule_time", sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column("schedule_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("schedule_minutes")
        batch_op.drop_column("schedule_time")
        batch_op.drop_column("schedule_days")
