"""Keep dispatch accounting indexed by task, active slots and unresolved exposure."""

from alembic import op

revision = "0012_admission_indexes"
down_revision = "0011_recovery_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index builds can outlast the runtime statement limit on retained history.
    # Keep the short lock timeout so a busy database rejects the migration.
    op.execute("SET LOCAL statement_timeout = '5min'")
    op.execute("""
        CREATE INDEX ix_call_task ON external_calls(task_id);
        CREATE INDEX ix_call_active_owner ON external_calls(principal_id) WHERE local_active;
        CREATE INDEX ix_cost_unresolved ON call_costs(call_id)
            WHERE exposure_cost>0 OR exposure_tokens>0 OR overrun;
        CREATE INDEX ix_principal_reserve ON principals(id)
            WHERE reserve_cost>0 OR reserve_tokens>0;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX ix_principal_reserve,ix_cost_unresolved,ix_call_active_owner,ix_call_task;
    """)
