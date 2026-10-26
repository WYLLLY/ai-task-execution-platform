"""Safe public usage provenance, retained ownership and durable trace context."""

from alembic import op

revision = "0006_operations"
down_revision = "0005_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE call_costs ADD COLUMN estimated boolean NOT NULL DEFAULT false;
        UPDATE call_costs c SET estimated=coalesce((
            SELECT (d.details->>'estimated')::boolean FROM accounting_decisions d
            WHERE d.call_id=c.call_id AND d.kind='settlement'
              AND (d.details->>'final')::boolean
              AND (d.details->>'cost')::bigint=c.confirmed_cost
              AND (d.details->>'tokens')::bigint=c.confirmed_tokens
              AND d.revision>=c.revision-1
            ORDER BY d.created_at DESC,d.evidence_id DESC LIMIT 1),false);
        GRANT UPDATE(estimated) ON call_costs TO atp_worker,atp_admin;
        ALTER TABLE attempts ADD COLUMN trace_context jsonb CHECK(trace_context IS NULL OR ((
            jsonb_typeof(trace_context)='object'
            AND octet_length(trace_context::text)<=256
            AND jsonb_typeof(trace_context->'trace_id')='string'
            AND jsonb_typeof(trace_context->'span_id')='string'
            AND jsonb_typeof(trace_context->'flags')='string'
            AND trace_context->>'trace_id' ~ '^[0-9a-f]{32}$'
            AND trace_context->>'trace_id'!='00000000000000000000000000000000'
            AND trace_context->>'span_id' ~ '^[0-9a-f]{16}$'
            AND trace_context->>'span_id'!='0000000000000000'
            AND trace_context->>'flags' IN ('00','01','02','03')
            AND trace_context-ARRAY['trace_id','span_id','flags','previous_span_id']='{}'::jsonb
            AND (trace_context->>'previous_span_id' IS NULL OR
                 (jsonb_typeof(trace_context->'previous_span_id')='string'
                 AND trace_context->>'previous_span_id' ~ '^[0-9a-f]{16}$'
                 AND trace_context->>'previous_span_id'!='0000000000000000'))) IS TRUE));
        CREATE FUNCTION guard_attempt_trace() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.trace_context IS DISTINCT FROM OLD.trace_context THEN
                RAISE EXCEPTION 'Immutable attempt trace' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_attempt_trace BEFORE UPDATE ON attempts
            FOR EACH ROW EXECUTE FUNCTION guard_attempt_trace();
        CREATE TABLE task_tombstones (
            task_id uuid PRIMARY KEY,
            principal_id uuid NOT NULL REFERENCES principals(id),
            purged_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX tombstone_owner ON task_tombstones(principal_id,task_id);
        REVOKE ALL ON task_tombstones FROM PUBLIC;
        GRANT SELECT ON task_tombstones TO atp_api,atp_worker,atp_admin;
        GRANT INSERT ON task_tombstones TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE task_tombstones;
        DROP TRIGGER immutable_attempt_trace ON attempts;
        DROP FUNCTION guard_attempt_trace();
        ALTER TABLE attempts DROP COLUMN trace_context;
        ALTER TABLE call_costs DROP COLUMN estimated;
    """)
