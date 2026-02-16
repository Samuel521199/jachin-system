"""Initial schema v3.2

Revision ID: 001_initial
Revises: 
Create Date: 2026-02-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建用户表
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('username', sa.String(255), nullable=False, unique=True),
        sa.Column('email', sa.String(255), nullable=True, unique=True),
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('role', sa.String(50), nullable=False, server_default='user'),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
    )
    op.create_index('idx_users_username', 'users', ['username'])
    op.create_index('idx_users_email', 'users', ['email'])

    # 创建技能表
    op.create_table(
        'skills',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('skill_id', sa.String(255), nullable=False, unique=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('version', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('author', sa.String(255), nullable=True),
        sa.Column('license', sa.String(50), nullable=True),
        sa.Column('runtime', sa.String(50), nullable=False),
        sa.Column('entrypoint', sa.String(255), nullable=True),
        sa.Column('manifest_path', sa.Text(), nullable=False),
        sa.Column('install_path', sa.Text(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='installed'),
        sa.Column('installed_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('last_used_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('idx_skills_skill_id', 'skills', ['skill_id'])
    op.create_index('idx_skills_status', 'skills', ['status'])

    # 创建技能能力映射表
    op.create_table(
        'skill_capabilities',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('capability_name', sa.String(255), nullable=False),
        sa.Column('capability_type', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('input_schema', postgresql.JSONB(), nullable=True),
        sa.Column('output_schema', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('skill_id', 'capability_name', name='idx_skill_capabilities_unique'),
    )
    op.create_index('idx_skill_capabilities_skill_id', 'skill_capabilities', ['skill_id'])
    op.create_index('idx_skill_capabilities_name', 'skill_capabilities', ['capability_name'])
    op.create_index('idx_skill_capabilities_type', 'skill_capabilities', ['capability_type'])

    # 创建记忆表
    op.create_table(
        'memories',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_type', sa.String(50), nullable=False),
        sa.Column('vector_id', sa.String(255), nullable=True),
        sa.Column('collection_name', sa.String(255), nullable=True),
        sa.Column('permission_level', sa.String(50), nullable=False, server_default='private'),
        sa.Column('meta_data', postgresql.JSONB(), nullable=True),  # Renamed from 'metadata' to avoid SQLAlchemy conflict
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('idx_memories_user_id', 'memories', ['user_id'])
    op.create_index('idx_memories_permission', 'memories', ['permission_level'])
    op.create_index('idx_memories_created_at', 'memories', ['created_at'])

    # 创建记忆权限表
    op.create_table(
        'memory_permissions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('memory_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('permission_type', sa.String(50), nullable=False),
        sa.Column('granted_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('granted_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['memory_id'], ['memories.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id']),
        sa.UniqueConstraint('memory_id', 'user_id', 'permission_type', name='idx_memory_permissions_unique'),
    )
    op.create_index('idx_memory_permissions_memory_id', 'memory_permissions', ['memory_id'])
    op.create_index('idx_memory_permissions_user_id', 'memory_permissions', ['user_id'])

    # 创建任务表
    op.create_table(
        'tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('task_id', sa.String(255), nullable=False, unique=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('task_type', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('capability_name', sa.String(255), nullable=True),
        sa.Column('input_data', postgresql.JSONB(), nullable=True),
        sa.Column('output_data', postgresql.JSONB(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('worker_node', sa.String(255), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('started_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id']),
    )
    op.create_index('idx_tasks_task_id', 'tasks', ['task_id'])
    op.create_index('idx_tasks_user_id', 'tasks', ['user_id'])
    op.create_index('idx_tasks_status', 'tasks', ['status'])
    op.create_index('idx_tasks_created_at', 'tasks', ['created_at'])

    # 创建集群节点表
    op.create_table(
        'cluster_nodes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('node_id', sa.String(255), nullable=False, unique=True),
        sa.Column('node_type', sa.String(50), nullable=False),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('ray_port', sa.Integer(), nullable=True),
        sa.Column('dapr_port', sa.Integer(), nullable=True),
        sa.Column('has_gpu', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('gpu_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gpu_memory_gb', sa.Integer(), nullable=True),
        sa.Column('cpu_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('memory_gb', sa.Integer(), nullable=True),
        sa.Column('disk_gb', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='offline'),
        sa.Column('last_heartbeat', sa.TIMESTAMP(), nullable=True),
        sa.Column('registered_at', sa.TIMESTAMP(), server_default=sa.text('NOW()')),
        sa.Column('meta_data', postgresql.JSONB(), nullable=True),  # Renamed from 'metadata' to avoid SQLAlchemy conflict
    )
    op.create_index('idx_cluster_nodes_node_id', 'cluster_nodes', ['node_id'])
    op.create_index('idx_cluster_nodes_status', 'cluster_nodes', ['status'])
    op.create_index('idx_cluster_nodes_type', 'cluster_nodes', ['node_type'])


def downgrade() -> None:
    # 删除所有表（按依赖顺序反向）
    op.drop_table('cluster_nodes')
    op.drop_table('tasks')
    op.drop_table('memory_permissions')
    op.drop_table('memories')
    op.drop_table('skill_capabilities')
    op.drop_table('skills')
    op.drop_table('users')
