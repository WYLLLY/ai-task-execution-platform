"""Fenced restore quarantine and minimal audited disaster exposure, without invented calls."""

from alembic import op

revision = "0010_recovery"
down_revision = "0009_corpus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE platform_control ADD COLUMN recovery jsonb,
            ADD COLUMN reserve_cost bigint NOT NULL DEFAULT 0 CHECK(reserve_cost>=0),
            ADD COLUMN reserve_tokens bigint NOT NULL DEFAULT 0 CHECK(reserve_tokens>=0),
            ADD COLUMN reserve_revision bigint NOT NULL DEFAULT 0 CHECK(reserve_revision>=0);
        ALTER TABLE principals
            ADD COLUMN reserve_cost bigint NOT NULL DEFAULT 0 CHECK(reserve_cost>=0),
            ADD COLUMN reserve_tokens bigint NOT NULL DEFAULT 0 CHECK(reserve_tokens>=0);
        ALTER TABLE tasks ADD COLUMN restore_uncertain boolean NOT NULL DEFAULT false;
        CREATE TABLE restore_decisions(
            epoch uuid NOT NULL, evidence_id varchar(90) NOT NULL,
            fingerprint varchar(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
            kind varchar(20) NOT NULL CHECK(kind IN('begin','reserve','reopen','release')),
            details jsonb NOT NULL CHECK(octet_length(details::text)<=65536),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(epoch,evidence_id));
        CREATE TRIGGER immutable_restore_evidence BEFORE UPDATE OR DELETE ON restore_decisions
            FOR EACH ROW EXECUTE FUNCTION guard_evidence();
        REVOKE ALL ON restore_decisions FROM PUBLIC;
        GRANT SELECT,INSERT ON restore_decisions TO atp_admin;
        CREATE FUNCTION quarantine_restore(new_epoch uuid, evidence jsonb) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        DECLARE moment timestamptz; affected integer;
        BEGIN
            PERFORM 1 FROM public.platform_control WHERE id=1 FOR UPDATE;
            IF (evidence->>'old_workers_isolated')::boolean IS DISTINCT FROM true OR
                (evidence->>'credentials_revoked')::boolean IS DISTINCT FROM true OR
                (evidence->>'live_egress_blocked')::boolean IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Isolation evidence required' USING ERRCODE='23514'; END IF;
            IF EXISTS(SELECT 1 FROM public.platform_control WHERE execution_epoch=new_epoch)
                THEN RAISE EXCEPTION 'New epoch required' USING ERRCODE='23514'; END IF;
            PERFORM 1 FROM public.principals ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.api_keys ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.provider_admission ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.tasks ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.steps ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.attempts ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.external_calls ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.call_costs ORDER BY call_id FOR UPDATE;
            moment=clock_timestamp();
            UPDATE public.api_keys SET revoked_at=moment,permission_version=permission_version+1
                WHERE revoked_at IS NULL;
            UPDATE public.attempts SET state='revoked',ended_at=moment,reason='restore_quarantine'
                WHERE state='active';
            UPDATE public.external_calls SET state=CASE WHEN state='authorized' THEN 'unknown'
                ELSE state END,local_active=false WHERE local_active OR state='authorized';
            UPDATE public.steps s SET state=CASE WHEN t.deadline_at<=moment THEN 'cancelled'
                WHEN EXISTS(SELECT 1 FROM public.external_calls x WHERE x.id=s.selected_call_id
                    AND x.state='unknown') THEN 'unknown' ELSE 'ready' END,
                generation=generation+1,holder=NULL,lease_until=NULL
                FROM public.tasks t WHERE s.task_id=t.id
                AND t.state IN('queued','running','suspended')
                AND s.state IN('ready','running','retry_wait','unknown');
            WITH changed AS (
                UPDATE public.tasks SET state=CASE WHEN deadline_at<=moment THEN 'timed_out'
                    ELSE 'suspended' END, reason='restore_quarantine',restore_uncertain=true,
                    task_version=task_version+1,event_sequence=event_sequence+1
                WHERE state IN('queued','running','suspended')
                RETURNING id,event_sequence,principal_id,state,task_version
            ) INSERT INTO public.task_events(task_id,sequence,principal_id,kind,data)
                SELECT id,event_sequence,principal_id,'task.restore_quarantine',
                    jsonb_build_object('state',state,'task_version',task_version) FROM changed;
            GET DIAGNOSTICS affected=ROW_COUNT;
            UPDATE public.principals p SET open_tasks=(SELECT count(*) FROM public.tasks t
                WHERE t.principal_id=p.id AND t.state IN('queued','running','suspended'));
            UPDATE public.platform_control SET execution_epoch=new_epoch,
                admission_open=false,dispatch_open=false,version=version+1,
                recovery=evidence || jsonb_build_object('bounded',false,'quarantined',affected,
                    'acceptance_history_may_be_lost',true),
                open_tasks=(SELECT count(*) FROM public.tasks
                    WHERE state IN('queued','running','suspended')) WHERE id=1;
            RETURN jsonb_build_object('quarantined',affected);
        END $$;
        REVOKE ALL ON FUNCTION quarantine_restore(uuid,jsonb) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION quarantine_restore(uuid,jsonb) TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION quarantine_restore(uuid,jsonb);
        DROP TABLE restore_decisions;
        ALTER TABLE tasks DROP COLUMN restore_uncertain;
        ALTER TABLE principals DROP COLUMN reserve_cost,DROP COLUMN reserve_tokens;
        ALTER TABLE platform_control DROP COLUMN recovery,DROP COLUMN reserve_cost,
            DROP COLUMN reserve_tokens,DROP COLUMN reserve_revision;
    """)
