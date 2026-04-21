"""expand id cols to 36 for uuid with hyphens

Revision ID: 71024c59a7f8
Revises: 275f412862a5
Create Date: 2026-04-21 11:00:01.600287

SQLite 不支持原生 ALTER COLUMN，所以用 batch_alter_table 走"建临时表+拷贝"流程。
Postgres/MySQL 下 batch 模式会自动降级为普通 ALTER，不影响。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '71024c59a7f8'
down_revision: Union[str, Sequence[str], None] = '275f412862a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('messages') as b:
        b.alter_column('id', existing_type=sa.VARCHAR(length=32),
                       type_=sa.String(length=36), existing_nullable=False)
        b.alter_column('session_id', existing_type=sa.VARCHAR(length=32),
                       type_=sa.String(length=36), existing_nullable=False)
    with op.batch_alter_table('sessions') as b:
        b.alter_column('id', existing_type=sa.VARCHAR(length=32),
                       type_=sa.String(length=36), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('sessions') as b:
        b.alter_column('id', existing_type=sa.String(length=36),
                       type_=sa.VARCHAR(length=32), existing_nullable=False)
    with op.batch_alter_table('messages') as b:
        b.alter_column('session_id', existing_type=sa.String(length=36),
                       type_=sa.VARCHAR(length=32), existing_nullable=False)
        b.alter_column('id', existing_type=sa.String(length=36),
                       type_=sa.VARCHAR(length=32), existing_nullable=False)
