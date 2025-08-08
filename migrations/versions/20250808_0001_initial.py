
"""initial schema for Ask Chip

Revision ID: 20250808_0001
Revises: 
Create Date: 2025-08-08 00:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

# revision identifiers, used by Alembic.
revision = '20250808_0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # users: minimal profile table used by login/profile flow
    op.create_table(
        'users',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Text, nullable=False, unique=True),  # typically email
        sa.Column('name', sa.Text, nullable=True),
        sa.Column('title', sa.Text, nullable=True),
        sa.Column('role', sa.Text, nullable=True),
        sa.Column('region', sa.Text, nullable=True),
        sa.Column('created_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Optional tables for training/chat sessions
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('user_id', sa.Text, nullable=False),
        sa.Column('topic', sa.Text, nullable=True),
        sa.Column('started_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ended_at', psql.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        'messages',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('session_id', sa.BigInteger, nullable=False),
        sa.Column('sender', sa.Text, nullable=False),  # 'user' | 'chip'
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('created_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'feedback',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('session_id', sa.BigInteger, nullable=True),
        sa.Column('rating', sa.Integer, nullable=True),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('created_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'logs',
        sa.Column('id', sa.BigInteger, primary_key=True),
        sa.Column('level', sa.Text, nullable=True),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('extra', psql.JSONB, nullable=True),
        sa.Column('created_at', psql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

def downgrade() -> None:
    op.drop_table('logs')
    op.drop_table('feedback')
    op.drop_table('messages')
    op.drop_table('chat_sessions')
    op.drop_table('users')
