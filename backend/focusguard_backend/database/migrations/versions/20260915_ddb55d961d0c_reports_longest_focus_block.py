"""reports: longest focus block

Phase 15. The recommendation rules need the longest unbroken focus stretch, and
it is stored rather than derived because the score samples it comes from are
pruned after 30 days (spec section 45).

Autogenerate produced this as NOT NULL with no default, which **fails on any
table that already has rows** - every existing session would violate the
constraint the moment it is added. A server_default of 0 backfills them, and it
is then dropped so the application supplies the value from here on.

Revision ID: ddb55d961d0c
Revises: ddd8f00fd2d0
Created: 2026-09-15 09:36:45.902600
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'ddb55d961d0c'
down_revision = 'ddd8f00fd2d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('focus_sessions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'longest_focus_block_seconds',
                sa.Float(),
                nullable=False,
                # Backfills existing rows. Zero is honest here: those sessions
                # were recorded before the agent measured this, so the truthful
                # answer is "not known", and zero is what an unmeasured session
                # already reports everywhere else.
                server_default='0',
            )
        )

    # A SECOND batch block, not a second operation inside the first. On SQLite
    # `batch_alter_table` collapses everything in one block into a single table
    # rebuild, so dropping the default alongside the add removes it *before* the
    # rows are copied - and every existing row then violates NOT NULL. Verified
    # by actually running this against a populated database.
    with op.batch_alter_table('focus_sessions', schema=None) as batch_op:
        batch_op.alter_column('longest_focus_block_seconds', server_default=None)



def downgrade() -> None:
    with op.batch_alter_table('focus_sessions', schema=None) as batch_op:
        batch_op.drop_column('longest_focus_block_seconds')
