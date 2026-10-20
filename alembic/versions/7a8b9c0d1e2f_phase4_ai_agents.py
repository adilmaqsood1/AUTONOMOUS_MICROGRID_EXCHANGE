"""phase4_ai_agents

Revision ID: 7a8b9c0d1e2f
Revises: 5756ad2713b8
Create Date: 2026-10-08 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.base

# revision identifiers, used by Alembic.
revision: str = '7a8b9c0d1e2f'
down_revision: Union[str, None] = '5756ad2713b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ai_agent_configurations',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('participant_id', sa.Uuid(), nullable=True),
        sa.Column('agent_id', sa.String(length=80), nullable=False),
        sa.Column('agent_type', sa.String(length=40), nullable=False),
        sa.Column('strategy_mode', sa.String(length=20), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('model_name', sa.String(length=60), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('config_json', sa.JSON(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.ForeignKeyConstraint(['participant_id'], ['participants.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_agent_configurations_agent_id'), 'ai_agent_configurations', ['agent_id'], unique=False)
    op.create_index(op.f('ix_ai_agent_configurations_microgrid_id'), 'ai_agent_configurations', ['microgrid_id'], unique=False)
    op.create_index(op.f('ix_ai_agent_configurations_participant_id'), 'ai_agent_configurations', ['participant_id'], unique=False)

    op.create_table('agent_memories',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('agent_id', sa.String(length=80), nullable=False),
        sa.Column('summary_stats', sa.JSON(), nullable=False),
        sa.Column('episodes', sa.JSON(), nullable=False),
        sa.Column('updated_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_memories_agent_id'), 'agent_memories', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_memories_microgrid_id'), 'agent_memories', ['microgrid_id'], unique=False)

    op.create_table('agent_decisions',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('agent_id', sa.String(length=80), nullable=False),
        sa.Column('agent_type', sa.String(length=40), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('market_round_id', sa.String(length=64), nullable=False),
        sa.Column('observation_hash', sa.String(length=64), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=False),
        sa.Column('proposal', sa.JSON(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('reasoning_summary', sa.String(length=500), nullable=False),
        sa.Column('assumptions', sa.JSON(), nullable=False),
        sa.Column('risk_factors', sa.JSON(), nullable=False),
        sa.Column('guard_status', sa.String(length=20), nullable=False),
        sa.Column('guard_reason_codes', sa.JSON(), nullable=False),
        sa.Column('fallback_used', sa.Boolean(), nullable=False),
        sa.Column('fallback_reason', sa.String(length=200), nullable=True),
        sa.Column('final_action', sa.String(length=40), nullable=False),
        sa.Column('final_quantity', sa.Float(), nullable=False),
        sa.Column('final_price', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_decisions_agent_id'), 'agent_decisions', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_decisions_market_round_id'), 'agent_decisions', ['market_round_id'], unique=False)
    op.create_index(op.f('ix_agent_decisions_microgrid_id'), 'agent_decisions', ['microgrid_id'], unique=False)
    op.create_index('ix_agent_decisions_round', 'agent_decisions', ['market_round_id', 'agent_id'], unique=False)

    op.create_table('agent_outcomes',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('agent_id', sa.String(length=80), nullable=False),
        sa.Column('decision_id', sa.Uuid(), nullable=True),
        sa.Column('market_round_id', sa.String(length=64), nullable=False),
        sa.Column('proposed_action', sa.String(length=40), nullable=False),
        sa.Column('executed_action', sa.String(length=40), nullable=False),
        sa.Column('proposed_quantity', sa.Float(), nullable=False),
        sa.Column('executed_quantity', sa.Float(), nullable=False),
        sa.Column('market_price', sa.Float(), nullable=False),
        sa.Column('local_lmp', sa.Float(), nullable=False),
        sa.Column('energy_bought', sa.Float(), nullable=False),
        sa.Column('energy_sold', sa.Float(), nullable=False),
        sa.Column('energy_revenue', sa.Float(), nullable=False),
        sa.Column('energy_cost', sa.Float(), nullable=False),
        sa.Column('reserve_revenue', sa.Float(), nullable=False),
        sa.Column('demand_response_revenue', sa.Float(), nullable=False),
        sa.Column('battery_degradation_cost', sa.Float(), nullable=False),
        sa.Column('shortfall_penalty', sa.Float(), nullable=False),
        sa.Column('other_penalties', sa.Float(), nullable=False),
        sa.Column('net_reward', sa.Float(), nullable=False),
        sa.Column('counterfactual_reward', sa.Float(), nullable=True),
        sa.Column('delivery_ratio', sa.Float(), nullable=False),
        sa.Column('forecast_error', sa.Float(), nullable=False),
        sa.Column('guard_intervention', sa.Boolean(), nullable=False),
        sa.Column('fallback_used', sa.Boolean(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['decision_id'], ['agent_decisions.id']),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_outcomes_agent_id'), 'agent_outcomes', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_outcomes_market_round_id'), 'agent_outcomes', ['market_round_id'], unique=False)
    op.create_index(op.f('ix_agent_outcomes_microgrid_id'), 'agent_outcomes', ['microgrid_id'], unique=False)
    op.create_index('ix_agent_outcomes_round', 'agent_outcomes', ['market_round_id', 'agent_id'], unique=False)

    op.create_table('agent_market_rounds',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('round_id', sa.String(length=64), nullable=False),
        sa.Column('snapshot_timestamp', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('participating_agents', sa.JSON(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_market_rounds_microgrid_id'), 'agent_market_rounds', ['microgrid_id'], unique=False)
    op.create_index(op.f('ix_agent_market_rounds_round_id'), 'agent_market_rounds', ['round_id'], unique=True)

    op.create_table('agent_experiments',
        sa.Column('microgrid_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('scenario', sa.String(length=40), nullable=False),
        sa.Column('seed', sa.Integer(), nullable=False),
        sa.Column('agent_mode', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('duration_steps', sa.Integer(), nullable=False),
        sa.Column('results', sa.JSON(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_experiments_microgrid_id'), 'agent_experiments', ['microgrid_id'], unique=False)

    op.create_table('ai_provider_usages',
        sa.Column('microgrid_id', sa.Uuid(), nullable=True),
        sa.Column('agent_id', sa.String(length=80), nullable=False),
        sa.Column('market_round_id', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('estimated_cost', sa.Float(), nullable=False),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('created_at', app.db.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['microgrid_id'], ['microgrids.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_provider_usages_agent_id'), 'ai_provider_usages', ['agent_id'], unique=False)
    op.create_index(op.f('ix_ai_provider_usages_market_round_id'), 'ai_provider_usages', ['market_round_id'], unique=False)
    op.create_index(op.f('ix_ai_provider_usages_microgrid_id'), 'ai_provider_usages', ['microgrid_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ai_provider_usages')
    op.drop_table('agent_experiments')
    op.drop_table('agent_market_rounds')
    op.drop_table('agent_outcomes')
    op.drop_table('agent_decisions')
    op.drop_table('agent_memories')
    op.drop_table('ai_agent_configurations')
