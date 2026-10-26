"""Durable execution identities, fictitious-provider admission and provisions."""

from alembic import op

revision = "0003_execution"
down_revision = "0002_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_task() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND (
            (to_jsonb(NEW)-'event_sequence') IS DISTINCT FROM
            (to_jsonb(OLD)-'event_sequence') OR NEW.event_sequence != OLD.event_sequence+1)
        THEN RAISE EXCEPTION 'Terminal task immutable' USING ERRCODE='23514'; END IF;
        IF (NEW.id, NEW.principal_id, NEW.key_id, NEW.workflow_name, NEW.workflow_version,
            NEW.versions, NEW.input, NEW.limits, NEW.execution_epoch)
        IS DISTINCT FROM (OLD.id, OLD.principal_id, OLD.key_id, OLD.workflow_name,
            OLD.workflow_version, OLD.versions, OLD.input, OLD.limits, OLD.execution_epoch)
        THEN RAISE EXCEPTION 'Pinned task identity immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state IS DISTINCT FROM OLD.state AND (NEW.task_version != OLD.task_version+1 OR
            NOT (
        (OLD.state='queued' AND NEW.state IN ('running', 'suspended', 'failed', 'cancelled',
            'timed_out')) OR
        (OLD.state='running' AND NEW.state IN ('succeeded', 'failed', 'suspended', 'cancelled',
            'timed_out')) OR
        (OLD.state='suspended' AND NEW.state IN ('queued', 'succeeded', 'failed', 'cancelled',
            'timed_out'))))
        THEN RAISE EXCEPTION 'Illegal task transition' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$;
        ALTER TABLE steps
            ADD COLUMN available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN selected_call_id uuid,
            ADD COLUMN result jsonb,
            ADD COLUMN failures integer NOT NULL DEFAULT 0 CHECK(failures>=0);
        ALTER TABLE attempts
            ADD COLUMN started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN ended_at timestamptz,
            ADD COLUMN reason varchar(80),
            ADD COLUMN worker varchar(100),
            ADD COLUMN execution_epoch uuid,
            ADD CONSTRAINT uq_attempt_identity UNIQUE(id, step_id, task_id);
        CREATE TABLE provider_admission (
            id integer PRIMARY KEY CHECK(id=1),
            requests_per_minute integer NOT NULL DEFAULT 100 CHECK(requests_per_minute>=0),
            tokens_per_minute bigint NOT NULL DEFAULT 1000000 CHECK(tokens_per_minute>=0),
            max_active integer NOT NULL DEFAULT 4 CHECK(max_active BETWEEN 0 AND 4),
            max_owner_active integer NOT NULL DEFAULT 2 CHECK(max_owner_active BETWEEN 0 AND 2),
            next_allowed_at timestamptz NOT NULL DEFAULT '2000-01-01T00:00:00Z',
            global_daily_cost bigint NOT NULL DEFAULT 100000000 CHECK(global_daily_cost>=0),
            owner_daily_cost bigint NOT NULL DEFAULT 10000000 CHECK(owner_daily_cost>=0),
            global_daily_tokens bigint NOT NULL DEFAULT 4000000 CHECK(global_daily_tokens>=0),
            owner_daily_tokens bigint NOT NULL DEFAULT 400000 CHECK(owner_daily_tokens>=0)
        );
        INSERT INTO provider_admission(id) VALUES(1);
        CREATE TABLE external_calls (
            id uuid PRIMARY KEY, task_id uuid NOT NULL, principal_id uuid NOT NULL,
            step_id uuid NOT NULL, attempt_id uuid NOT NULL,
            generation bigint NOT NULL CHECK(generation>0), execution_epoch uuid NOT NULL,
            principal_version bigint NOT NULL, key_version bigint NOT NULL,
            state varchar(20) NOT NULL CHECK(state IN
                ('authorized','succeeded','rejected','unknown')),
            payload bytea NOT NULL CHECK(octet_length(payload)<=1048576),
            payload_hash varchar(64) NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
            provider varchar(40) NOT NULL CHECK(provider='simulator-v1'),
            authorized_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            response_observed_at timestamptz,
            local_active boolean NOT NULL DEFAULT true,
            FOREIGN KEY(task_id,principal_id) REFERENCES tasks(id,principal_id),
            FOREIGN KEY(attempt_id,step_id,task_id) REFERENCES attempts(id,step_id,task_id),
            UNIQUE(id,step_id), UNIQUE(attempt_id)
        );
        CREATE INDEX ix_call_window ON external_calls(authorized_at);
        CREATE INDEX ix_call_owner ON external_calls(principal_id,authorized_at);
        ALTER TABLE steps ADD CONSTRAINT selected_call_same_step
            FOREIGN KEY(selected_call_id,id) REFERENCES external_calls(id,step_id)
            DEFERRABLE INITIALLY DEFERRED;
        CREATE TABLE call_costs (
            call_id uuid PRIMARY KEY REFERENCES external_calls(id),
            reserved_cost bigint NOT NULL CHECK(reserved_cost>=0),
            reserved_tokens bigint NOT NULL CHECK(reserved_tokens>=0),
            confirmed_cost bigint NOT NULL DEFAULT 0 CHECK(confirmed_cost>=0),
            confirmed_tokens bigint NOT NULL DEFAULT 0 CHECK(confirmed_tokens>=0),
            exposure_cost bigint NOT NULL CHECK(exposure_cost>=0),
            exposure_tokens bigint NOT NULL CHECK(exposure_tokens>=0),
            revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
            overrun boolean NOT NULL DEFAULT false,
            tariff varchar(40) NOT NULL CHECK(tariff='simulated-micro-usd-v1'),
            tokenizer varchar(40) NOT NULL CHECK(tokenizer='simulated-bytes-v1')
        );
        CREATE TABLE cost_evidence (
            call_id uuid NOT NULL REFERENCES call_costs(call_id),
            evidence_id varchar(100) NOT NULL CHECK(length(evidence_id)>0),
            fingerprint varchar(64) NOT NULL,
            expected_revision bigint NOT NULL CHECK(expected_revision>=0),
            cost bigint NOT NULL CHECK(cost>=0), tokens bigint NOT NULL CHECK(tokens>=0),
            final boolean NOT NULL,
            PRIMARY KEY(call_id,evidence_id)
        );
        CREATE FUNCTION guard_call() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW)-'state'-'local_active'-'response_observed_at') IS DISTINCT FROM
                (to_jsonb(OLD)-'state'-'local_active'-'response_observed_at')
                OR (OLD.response_observed_at IS NOT NULL AND
                    NEW.response_observed_at IS DISTINCT FROM OLD.response_observed_at)
                OR (NOT OLD.local_active AND NEW.local_active)
                OR (NEW.state!=OLD.state AND NOT (
                    (OLD.state='authorized' AND NEW.state IN('succeeded','rejected','unknown'))
                    OR (OLD.state='unknown' AND NEW.state IN('succeeded','rejected'))))
            THEN RAISE EXCEPTION 'Immutable call identity or illegal transition'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER call_arcs BEFORE UPDATE ON external_calls
            FOR EACH ROW EXECUTE FUNCTION guard_call();
        CREATE FUNCTION guard_cost_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.call_id,NEW.reserved_cost,NEW.reserved_tokens,NEW.tariff,NEW.tokenizer)
                IS DISTINCT FROM
                (OLD.call_id,OLD.reserved_cost,OLD.reserved_tokens,OLD.tariff,OLD.tokenizer)
            THEN RAISE EXCEPTION 'Immutable provision identity' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_cost_identity BEFORE UPDATE ON call_costs
            FOR EACH ROW EXECUTE FUNCTION guard_cost_identity();
        CREATE FUNCTION guard_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Immutable cost evidence' USING ERRCODE='23514';
        END $$;
        CREATE TRIGGER immutable_cost_evidence BEFORE UPDATE OR DELETE ON cost_evidence
            FOR EACH ROW EXECUTE FUNCTION guard_evidence();
        REVOKE ALL ON external_calls,call_costs,cost_evidence,provider_admission FROM PUBLIC;
        GRANT SELECT ON external_calls,call_costs,cost_evidence,provider_admission
            TO atp_api,atp_worker,atp_admin;
        GRANT INSERT ON external_calls,call_costs,cost_evidence TO atp_worker;
        GRANT UPDATE(state,local_active) ON external_calls TO atp_worker,atp_api;
        GRANT UPDATE(response_observed_at) ON external_calls TO atp_worker;
        GRANT UPDATE(confirmed_cost,confirmed_tokens,exposure_cost,exposure_tokens,revision,overrun)
            ON call_costs TO atp_worker;
        GRANT UPDATE(next_allowed_at) ON provider_admission TO atp_worker;
        GRANT UPDATE ON provider_admission TO atp_admin;
        GRANT INSERT ON attempts,steps,task_events TO atp_worker;
        GRANT UPDATE(state,reason,task_version,event_sequence,result,storage_bytes)
            ON tasks TO atp_worker;
        GRANT UPDATE(state,generation,holder,lease_until,available_at,
            selected_call_id,result,failures)
            ON steps TO atp_worker;
        GRANT UPDATE(state,ended_at,reason) ON attempts TO atp_worker,atp_api;
        GRANT UPDATE(available_at,selected_call_id) ON steps TO atp_api;
        GRANT UPDATE(open_tasks,storage_bytes) ON platform_control,principals TO atp_worker;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_task() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND NEW IS DISTINCT
            FROM OLD
        THEN RAISE EXCEPTION 'Terminal task immutable' USING ERRCODE='23514'; END IF;
        IF (NEW.id, NEW.principal_id, NEW.key_id, NEW.workflow_name, NEW.workflow_version,
            NEW.versions, NEW.input, NEW.limits, NEW.execution_epoch)
        IS DISTINCT FROM (OLD.id, OLD.principal_id, OLD.key_id, OLD.workflow_name,
            OLD.workflow_version, OLD.versions, OLD.input, OLD.limits, OLD.execution_epoch)
        THEN RAISE EXCEPTION 'Pinned task identity immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state IS DISTINCT FROM OLD.state AND (NEW.task_version != OLD.task_version+1 OR
            NOT (
        (OLD.state='queued' AND NEW.state IN ('running', 'suspended', 'failed', 'cancelled',
            'timed_out')) OR
        (OLD.state='running' AND NEW.state IN ('succeeded', 'failed', 'suspended', 'cancelled',
            'timed_out')) OR
        (OLD.state='suspended' AND NEW.state IN ('queued', 'succeeded', 'failed', 'cancelled',
            'timed_out'))))
        THEN RAISE EXCEPTION 'Illegal task transition' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$;
        ALTER TABLE steps DROP CONSTRAINT selected_call_same_step;
        DROP TABLE cost_evidence,call_costs,external_calls,provider_admission;
        DROP FUNCTION guard_evidence(),guard_cost_identity(),guard_call();
        ALTER TABLE steps DROP COLUMN available_at,DROP COLUMN selected_call_id,
            DROP COLUMN result,DROP COLUMN failures;
        ALTER TABLE attempts DROP CONSTRAINT uq_attempt_identity,
            DROP COLUMN started_at,DROP COLUMN ended_at,DROP COLUMN reason,
            DROP COLUMN worker,DROP COLUMN execution_epoch;
        REVOKE INSERT ON attempts,steps,task_events FROM atp_worker;
        REVOKE UPDATE ON tasks,steps,attempts,platform_control,principals FROM atp_worker;
    """)
