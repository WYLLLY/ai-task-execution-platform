"""Conservative retention clocks; controlled administrative redaction follows."""

from alembic import op

revision = "0007_retention"
down_revision = "0006_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tasks ADD COLUMN terminal_at timestamptz,
            ADD COLUMN purged_at timestamptz;
        -- Migration-only exclusive DDL lock: legacy termination time is unknown.
        -- Start a conservative clock now instead of guessing/backdating one.
        ALTER TABLE tasks DISABLE TRIGGER task_arcs;
        UPDATE tasks SET terminal_at=clock_timestamp()
            WHERE state IN('succeeded','failed','cancelled','timed_out');
        ALTER TABLE tasks ENABLE TRIGGER task_arcs;
        ALTER TABLE tasks ADD CONSTRAINT terminal_clock_present CHECK(
            (state IN('succeeded','failed','cancelled','timed_out'))=(terminal_at IS NOT NULL));
        CREATE FUNCTION stamp_task_terminal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
                IF NEW.state IN('succeeded','failed','cancelled','timed_out') THEN
                    NEW.terminal_at=clock_timestamp();
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER a_task_terminal_clock BEFORE INSERT OR UPDATE ON tasks
            FOR EACH ROW EXECUTE FUNCTION stamp_task_terminal();
        ALTER TABLE idempotency_records ADD COLUMN created_at timestamptz NOT NULL
            DEFAULT clock_timestamp(), ADD COLUMN response_purged_at timestamptz;
        ALTER TABLE call_costs ADD COLUMN resolved_at timestamptz;
        UPDATE call_costs SET resolved_at=clock_timestamp()
            WHERE exposure_cost=0 AND exposure_tokens=0 AND NOT estimated;
        CREATE FUNCTION stamp_cost_resolution() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.exposure_cost=0 AND NEW.exposure_tokens=0 AND NOT NEW.estimated THEN
                IF OLD.resolved_at IS NULL OR
                    (NEW.confirmed_cost,NEW.confirmed_tokens,NEW.estimated) IS DISTINCT FROM
                    (OLD.confirmed_cost,OLD.confirmed_tokens,OLD.estimated) THEN
                    NEW.resolved_at=clock_timestamp();
                END IF;
            ELSE NEW.resolved_at=NULL;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER cost_resolution_clock BEFORE UPDATE ON call_costs
            FOR EACH ROW EXECUTE FUNCTION stamp_cost_resolution();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER cost_resolution_clock ON call_costs;
        DROP FUNCTION stamp_cost_resolution();
        ALTER TABLE call_costs DROP COLUMN resolved_at;
        ALTER TABLE idempotency_records DROP COLUMN created_at,DROP COLUMN response_purged_at;
        DROP TRIGGER a_task_terminal_clock ON tasks;
        DROP FUNCTION stamp_task_terminal();
        ALTER TABLE tasks DROP CONSTRAINT terminal_clock_present;
        ALTER TABLE tasks DROP COLUMN terminal_at,DROP COLUMN purged_at;
    """)
