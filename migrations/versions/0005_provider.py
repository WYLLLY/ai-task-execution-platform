"""Responses checkpoints and immutable administrative accounting decisions."""

from alembic import op

revision = "0005_provider"
down_revision = "0004_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check;
        ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check CHECK(action IN
            ('principal.created','principal.disabled','key.created','key.revoked',
             'platform.initialized','platform.admission','workflow.availability',
             'provider.configured','accounting.settlement','accounting.acknowledgement'));
        ALTER TABLE external_calls DROP CONSTRAINT external_calls_provider_check;
        ALTER TABLE external_calls ADD CONSTRAINT external_calls_provider_check
            CHECK(provider IN ('simulator-v1','demo-v1','openai-v1'));
        ALTER TABLE call_costs DROP CONSTRAINT call_costs_tariff_check;
        ALTER TABLE call_costs ADD CONSTRAINT call_costs_tariff_check
            CHECK(tariff IN ('simulated-micro-usd-v1','gpt54mini-2026-10-23'));
        ALTER TABLE call_costs DROP CONSTRAINT call_costs_tokenizer_check;
        ALTER TABLE call_costs ADD CONSTRAINT call_costs_tokenizer_check
            CHECK(tokenizer IN ('simulated-bytes-v1','o200k_base-tiktoken-0.14.0'));
        ALTER TABLE steps ADD COLUMN protocol_items jsonb;
        ALTER TABLE external_calls ADD COLUMN provider_observation jsonb
            CHECK(octet_length(provider_observation::text)<=4096);
        GRANT UPDATE(provider_observation) ON external_calls TO atp_worker;
        CREATE OR REPLACE FUNCTION guard_call() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW)-'state'-'local_active'-'response_observed_at'-'provider_observation')
                IS DISTINCT FROM
                (to_jsonb(OLD)-'state'-'local_active'-'response_observed_at'-'provider_observation')
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

        CREATE FUNCTION guard_provider_observation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.provider_observation IS NOT NULL AND
                NEW.provider_observation IS DISTINCT FROM OLD.provider_observation
            THEN RAISE EXCEPTION 'Immutable provider observation' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_provider_observation BEFORE UPDATE ON external_calls
            FOR EACH ROW EXECUTE FUNCTION guard_provider_observation();
        GRANT UPDATE(protocol_items) ON steps TO atp_worker;
        CREATE TABLE accounting_decisions (
            call_id uuid NOT NULL REFERENCES external_calls(id),
            evidence_id varchar(100) NOT NULL,
            kind varchar(20) NOT NULL CHECK(kind IN ('settlement','acknowledgement')),
            revision bigint NOT NULL CHECK(revision>=0),
            fingerprint varchar(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
            details jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(call_id,evidence_id)
        );
        CREATE TRIGGER immutable_accounting_decision BEFORE UPDATE OR DELETE
            ON accounting_decisions FOR EACH ROW EXECUTE FUNCTION guard_evidence();
        REVOKE ALL ON accounting_decisions FROM PUBLIC;
        GRANT SELECT ON accounting_decisions TO atp_admin,atp_worker;
        GRANT INSERT ON accounting_decisions TO atp_admin;
        GRANT SELECT(call_id,kind,revision) ON accounting_decisions TO atp_api;
        GRANT UPDATE(response_observed_at) ON external_calls TO atp_admin;
        GRANT INSERT ON cost_evidence,task_events,workflow_versions TO atp_admin;
        GRANT UPDATE(event_sequence) ON tasks TO atp_admin;
        GRANT UPDATE(confirmed_cost,confirmed_tokens,exposure_cost,exposure_tokens,revision,overrun)
            ON call_costs TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("""
        REVOKE INSERT ON cost_evidence,task_events,workflow_versions FROM atp_admin;
        REVOKE UPDATE(event_sequence) ON tasks FROM atp_admin;
        REVOKE UPDATE(confirmed_cost,confirmed_tokens,exposure_cost,
            exposure_tokens,revision,overrun)
            ON call_costs FROM atp_admin;
        REVOKE UPDATE(response_observed_at) ON external_calls FROM atp_admin;
        DROP TRIGGER IF EXISTS immutable_provider_observation ON external_calls;
        DROP FUNCTION IF EXISTS guard_provider_observation();
        ALTER TABLE external_calls DROP COLUMN IF EXISTS provider_observation;
        CREATE OR REPLACE FUNCTION guard_call() RETURNS trigger LANGUAGE plpgsql AS $$
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

        DROP TABLE accounting_decisions;
        DELETE FROM audit_events WHERE action IN
            ('provider.configured','accounting.settlement','accounting.acknowledgement');
        ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check;
        ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check CHECK(action IN
            ('principal.created','principal.disabled','key.created','key.revoked',
             'platform.initialized','platform.admission','workflow.availability'));
        ALTER TABLE steps DROP COLUMN protocol_items;
        ALTER TABLE call_costs DROP CONSTRAINT call_costs_tariff_check;
        ALTER TABLE call_costs ADD CONSTRAINT call_costs_tariff_check
            CHECK(tariff='simulated-micro-usd-v1');
        ALTER TABLE call_costs DROP CONSTRAINT call_costs_tokenizer_check;
        ALTER TABLE call_costs ADD CONSTRAINT call_costs_tokenizer_check
            CHECK(tokenizer='simulated-bytes-v1');
        ALTER TABLE external_calls DROP CONSTRAINT external_calls_provider_check;
        ALTER TABLE external_calls ADD CONSTRAINT external_calls_provider_check
            CHECK(provider IN ('simulator-v1','demo-v1'));
    """)
