"""M2 admission, immutable command responses and constrained task state foundation."""

from alembic import op

revision = "0002_commands"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check;
        ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check CHECK(action IN
            ('principal.created', 'principal.disabled', 'key.created', 'key.revoked',
            'platform.initialized', 'platform.admission', 'workflow.availability'));
        ALTER TABLE principals ADD COLUMN open_tasks integer NOT NULL DEFAULT 0 CHECK(open_tasks
            >= 0),
        ADD COLUMN max_open_tasks integer NOT NULL DEFAULT 5 CHECK(max_open_tasks BETWEEN 0 AND
            5),
        ADD COLUMN storage_bytes bigint NOT NULL DEFAULT 0 CHECK(storage_bytes >= 0),
        ADD COLUMN max_storage_bytes bigint NOT NULL DEFAULT 104857600 CHECK(max_storage_bytes
            BETWEEN 0 AND 104857600);
        ALTER TABLE platform_control ADD COLUMN open_tasks integer NOT NULL DEFAULT 0
            CHECK(open_tasks >= 0),
        ADD COLUMN max_open_tasks integer NOT NULL DEFAULT 100 CHECK(max_open_tasks BETWEEN 0
            AND 100),
        ADD COLUMN storage_bytes bigint NOT NULL DEFAULT 0 CHECK(storage_bytes >= 0),
        ADD COLUMN max_storage_bytes bigint NOT NULL DEFAULT 2147483648 CHECK(max_storage_bytes
            BETWEEN 0 AND 2147483648);
        ALTER TABLE api_keys ADD CONSTRAINT uq_key_owner UNIQUE(id, principal_id);
        CREATE TABLE workflow_versions (
        name varchar(80) NOT NULL,  version varchar(40) NOT NULL,  available boolean NOT NULL,
        definition jsonb NOT NULL CHECK(jsonb_typeof(definition)='object'),  PRIMARY KEY(name,
            version)
        );
        INSERT INTO workflow_versions VALUES ('analysis', '1', true,
        '{"workflow":"1","prompt":"1","tools":"1","context_builder":"1"}');
        CREATE TABLE tasks (
        id uuid PRIMARY KEY,  principal_id uuid NOT NULL REFERENCES principals(id),
        key_id uuid NOT NULL,  workflow_name varchar(80) NOT NULL,  workflow_version varchar(40)
            NOT NULL,
        versions jsonb NOT NULL CHECK(jsonb_typeof(versions)='object'),
        input jsonb NOT NULL CHECK(jsonb_typeof(input)='object'),
        limits jsonb NOT NULL CHECK(jsonb_typeof(limits)='object'),  execution_epoch uuid NOT
            NULL,
        state varchar(20) NOT NULL CHECK(state IN ('queued', 'running', 'suspended',
            'succeeded', 'failed', 'cancelled', 'timed_out')),
        reason varchar(80),  task_version bigint NOT NULL DEFAULT 1 CHECK(task_version>0),
        event_sequence bigint NOT NULL DEFAULT 1 CHECK(event_sequence>0),
        oldest_retained_sequence bigint NOT NULL DEFAULT 1 CHECK(oldest_retained_sequence
            BETWEEN 1 AND event_sequence+1),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),  deadline_at timestamptz NOT
            NULL,
        result jsonb,  storage_bytes bigint NOT NULL CHECK(storage_bytes>=0),
        CHECK(deadline_at>created_at),  CHECK(state!='suspended' OR reason IS NOT NULL),
        UNIQUE(id, principal_id),  FOREIGN KEY(key_id, principal_id) REFERENCES api_keys(id,
            principal_id),
        FOREIGN KEY(workflow_name, workflow_version) REFERENCES workflow_versions(name, version)
        );
        CREATE INDEX ix_task_owner_page ON tasks(principal_id, created_at, id);
        CREATE TABLE steps (
        id uuid PRIMARY KEY,  task_id uuid NOT NULL,  principal_id uuid NOT NULL,
        path varchar(120) NOT NULL,  state varchar(20) NOT NULL CHECK(state IN ('ready',
            'running', 'succeeded', 'retry_wait', 'failed', 'unknown', 'cancelled')),
        generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),  holder varchar(100),
            lease_until timestamptz,
        CHECK((state='running' AND holder IS NOT NULL AND lease_until IS NOT NULL AND
            generation>0)
        OR (state!='running' AND holder IS NULL AND lease_until IS NULL)),
        UNIQUE(task_id, path),  UNIQUE(id, task_id),
        FOREIGN KEY(task_id, principal_id) REFERENCES tasks(id, principal_id)
        );
        CREATE UNIQUE INDEX uq_one_executable_step ON steps(task_id) WHERE state IN ('ready',
            'running', 'retry_wait', 'unknown');
        CREATE TABLE attempts (
        id uuid PRIMARY KEY,  task_id uuid NOT NULL,  step_id uuid NOT NULL,
        attempt_number integer NOT NULL CHECK(attempt_number>0),  generation bigint NOT NULL
            CHECK(generation>0),
        state varchar(20) NOT NULL CHECK(state IN ('active', 'succeeded', 'failed', 'expired',
            'revoked')),
        UNIQUE(step_id, attempt_number),  FOREIGN KEY(step_id, task_id) REFERENCES steps(id,
            task_id)
        );
        CREATE UNIQUE INDEX uq_active_attempt ON attempts(step_id) WHERE state='active';
        CREATE TABLE task_events (
        task_id uuid NOT NULL,  sequence bigint NOT NULL CHECK(sequence>0),  principal_id uuid
            NOT NULL,
        kind varchar(40) NOT NULL,  data jsonb NOT NULL CHECK(jsonb_typeof(data)='object'),
        PRIMARY KEY(task_id, sequence),  FOREIGN KEY(task_id, principal_id) REFERENCES tasks(id,
            principal_id)
        );
        CREATE TABLE idempotency_records (
        principal_id uuid NOT NULL,  route varchar(100) NOT NULL,  key varchar(128) NOT NULL
            CHECK(length(key)>0),
        canonical_version varchar(8) NOT NULL CHECK(canonical_version='v1'),
        fingerprint varchar(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),  task_id uuid
            NOT NULL,
        response_status integer NOT NULL CHECK(response_status IN (200, 202)),  response_body
            text NOT NULL,
        PRIMARY KEY(principal_id, route, key),  FOREIGN KEY(task_id, principal_id) REFERENCES
            tasks(id, principal_id)
        );
        CREATE FUNCTION guard_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF (NEW.name, NEW.version, NEW.definition) IS DISTINCT FROM (OLD.name, OLD.version,
            OLD.definition)
        THEN RAISE EXCEPTION 'Immutable workflow' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_workflow BEFORE UPDATE ON workflow_versions FOR EACH ROW
            EXECUTE FUNCTION guard_workflow();
        CREATE FUNCTION guard_task() RETURNS trigger LANGUAGE plpgsql AS $$
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
        CREATE TRIGGER task_arcs BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION
            guard_task();
        CREATE FUNCTION guard_step() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF OLD.state IN ('succeeded', 'failed', 'cancelled') AND NEW IS DISTINCT FROM OLD
        THEN RAISE EXCEPTION 'Terminal step immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
        (NEW.state='cancelled' AND OLD.state NOT IN ('succeeded', 'failed', 'cancelled')) OR
        (OLD.state='ready' AND NEW.state='running') OR
        (OLD.state='running' AND NEW.state IN ('succeeded', 'retry_wait', 'failed', 'unknown',
            'ready')) OR
        (OLD.state='retry_wait' AND NEW.state='ready') OR
        (OLD.state='unknown' AND NEW.state IN ('ready', 'succeeded', 'failed')))
        THEN RAISE EXCEPTION 'Illegal step transition' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$;
        CREATE TRIGGER step_arcs BEFORE UPDATE ON steps FOR EACH ROW EXECUTE FUNCTION
            guard_step();
        CREATE FUNCTION guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF OLD.state!='active' AND NEW IS DISTINCT FROM OLD
        THEN RAISE EXCEPTION 'Closed attempt immutable' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$;
        CREATE TRIGGER attempt_arcs BEFORE UPDATE ON attempts FOR EACH ROW EXECUTE FUNCTION
            guard_attempt();
        CREATE FUNCTION check_task_children() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; task_state text;
        BEGIN
        IF TG_TABLE_NAME='tasks' THEN target=NEW.id; ELSE target=NEW.task_id; END IF;
        SELECT state INTO task_state FROM tasks WHERE id=target;
        IF EXISTS (SELECT 1 FROM steps s WHERE s.task_id=target AND (
        (task_state IN ('succeeded', 'failed', 'cancelled', 'timed_out') AND s.state IN
            ('ready', 'running', 'retry_wait', 'unknown')) OR
        (task_state='queued' AND s.state IN ('running', 'retry_wait', 'unknown')) OR
        (task_state='suspended' AND s.state IN ('running', 'retry_wait')) OR
        (s.state='unknown' AND task_state!='suspended') OR
        ((s.state='running') IS DISTINCT FROM EXISTS(SELECT 1 FROM attempts a WHERE
            a.step_id=s.id AND a.state='active'))))
        THEN RAISE EXCEPTION 'Inconsistent task children' USING ERRCODE='23514'; END IF;
        RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER task_children AFTER INSERT OR UPDATE ON tasks DEFERRABLE
            INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_children();
        CREATE CONSTRAINT TRIGGER step_children AFTER INSERT OR UPDATE ON steps DEFERRABLE
            INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_children();
        CREATE CONSTRAINT TRIGGER attempt_children AFTER INSERT OR UPDATE ON attempts DEFERRABLE
            INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_task_children();
        REVOKE ALL ON workflow_versions, tasks, steps, attempts, task_events,
            idempotency_records FROM PUBLIC;
        GRANT SELECT ON workflow_versions, tasks, steps, attempts, task_events,
            idempotency_records TO atp_api, atp_worker, atp_admin;
        GRANT INSERT ON tasks, steps, task_events, idempotency_records TO atp_api;
        GRANT UPDATE(state, reason, task_version, event_sequence) ON tasks TO atp_api;
        GRANT UPDATE(state, generation, holder, lease_until) ON steps TO atp_api;
        GRANT UPDATE(state) ON attempts TO atp_api;
        GRANT UPDATE(open_tasks, storage_bytes) ON principals, platform_control TO atp_api;
        GRANT UPDATE(available) ON workflow_versions TO atp_admin;
        """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM audit_events WHERE action IN ('platform.admission',
            'workflow.availability');
        ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check;
        ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check CHECK(action IN
            ('principal.created', 'principal.disabled', 'key.created', 'key.revoked',
            'platform.initialized'));
        DROP TABLE idempotency_records, task_events, attempts, steps, tasks, workflow_versions;
        DROP FUNCTION check_task_children(), guard_attempt(), guard_step(), guard_task(),
            guard_workflow();
        ALTER TABLE api_keys DROP CONSTRAINT uq_key_owner;
        ALTER TABLE principals DROP COLUMN open_tasks, DROP COLUMN max_open_tasks, DROP COLUMN
            storage_bytes, DROP COLUMN max_storage_bytes;
        ALTER TABLE platform_control DROP COLUMN open_tasks, DROP COLUMN max_open_tasks, DROP
            COLUMN storage_bytes, DROP COLUMN max_storage_bytes;
        """)
