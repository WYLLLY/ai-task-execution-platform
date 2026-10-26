"""Immutable corpus and durable workflow checkpoints."""

from alembic import op

revision = "0004_workflow"
down_revision = "0003_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE external_calls DROP CONSTRAINT external_calls_provider_check;
        ALTER TABLE external_calls ADD CONSTRAINT external_calls_provider_check
            CHECK(provider IN ('simulator-v1','demo-v1'));
        CREATE TABLE documents (
            id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
            principal_id uuid NOT NULL REFERENCES principals(id),
            content text NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 1048576),
            content_hash varchar(64) NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
            readable boolean NOT NULL DEFAULT true,
            PRIMARY KEY(id,version), UNIQUE(id,version,principal_id)
        );
        CREATE INDEX document_owner ON documents(principal_id,id,version);
        CREATE FUNCTION guard_document() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' OR (to_jsonb(NEW)-'readable') IS DISTINCT FROM
                (to_jsonb(OLD)-'readable')
            THEN RAISE EXCEPTION 'Immutable document version' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_document BEFORE UPDATE OR DELETE ON documents
            FOR EACH ROW EXECUTE FUNCTION guard_document();
        REVOKE ALL ON documents FROM PUBLIC;
        GRANT SELECT ON documents TO atp_api,atp_worker,atp_admin;
        GRANT INSERT,UPDATE(readable) ON documents TO atp_admin;
        ALTER TABLE tasks ADD COLUMN tool_calls integer NOT NULL DEFAULT 0 CHECK(tool_calls>=0);
        GRANT UPDATE(tool_calls) ON tasks TO atp_worker;
        ALTER TABLE steps ADD COLUMN payload bytea,
            ADD COLUMN operation_key uuid,
            ADD COLUMN first_authorized_at timestamptz,
            ADD COLUMN probe_count integer NOT NULL DEFAULT 0 CHECK(probe_count BETWEEN 0 AND 3);
        GRANT UPDATE(payload,operation_key,first_authorized_at,probe_count) ON steps TO atp_worker;
        CREATE FUNCTION guard_step_payload() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (OLD.payload IS NOT NULL AND NEW.payload IS DISTINCT FROM OLD.payload)
                OR (OLD.operation_key IS NOT NULL
                    AND NEW.operation_key IS DISTINCT FROM OLD.operation_key)
                OR (OLD.first_authorized_at IS NOT NULL
                    AND NEW.first_authorized_at IS DISTINCT FROM OLD.first_authorized_at)
                OR NEW.probe_count < OLD.probe_count
            THEN RAISE EXCEPTION 'Immutable operation payload' USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_operation BEFORE UPDATE ON steps
            FOR EACH ROW EXECUTE FUNCTION guard_step_payload();
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE external_calls DROP CONSTRAINT external_calls_provider_check;
        ALTER TABLE external_calls ADD CONSTRAINT external_calls_provider_check
            CHECK(provider='simulator-v1');
        DROP TRIGGER immutable_operation ON steps;
        DROP FUNCTION guard_step_payload();
        ALTER TABLE steps DROP COLUMN payload,DROP COLUMN operation_key,
            DROP COLUMN first_authorized_at,DROP COLUMN probe_count;
        ALTER TABLE tasks DROP COLUMN tool_calls;
        DROP TABLE documents;
        DROP FUNCTION guard_document();
    """)
