"""Bounded administrator-only redaction, without financial cascades."""

from alembic import op
from sqlalchemy import text

revision = "0008_purge"
down_revision = "0007_retention"
branch_labels = None
depends_on = None


TASK_REDACTION = """
IF current_user='atp_migrator' AND OLD.state IN('succeeded','failed','cancelled','timed_out')
 AND OLD.terminal_at<=clock_timestamp()-interval '30 days'
 AND OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL AND NEW.input='{}'::jsonb
 AND NEW.result IS NULL AND NEW.oldest_retained_sequence=OLD.event_sequence+1
 AND NEW.storage_bytes BETWEEN 0 AND OLD.storage_bytes
 AND (to_jsonb(NEW)-ARRAY['input','result','purged_at','storage_bytes','oldest_retained_sequence'])
     =(to_jsonb(OLD)-ARRAY['input','result','purged_at','storage_bytes','oldest_retained_sequence'])
THEN RETURN NEW; END IF;
"""
STEP_REDACTION = """
IF current_user='atp_migrator' AND EXISTS(SELECT 1 FROM public.tasks t
 WHERE t.id=OLD.task_id AND t.purged_at IS NOT NULL)
 AND NEW.result IS NULL AND NEW.protocol_items IS NULL AND NEW.payload IS NULL
 AND (to_jsonb(NEW)-ARRAY['result','protocol_items','payload'])
     =(to_jsonb(OLD)-ARRAY['result','protocol_items','payload'])
THEN RETURN NEW; END IF;
"""
CALL_REDACTION = """
IF current_user='atp_migrator' AND EXISTS(SELECT 1 FROM public.tasks t
 WHERE t.id=OLD.task_id AND t.purged_at IS NOT NULL)
 AND NOT NEW.replay_available AND NEW.payload=''::bytea AND NEW.provider_observation IS NULL
 AND (to_jsonb(NEW)-ARRAY['payload','provider_observation','replay_available'])
     =(to_jsonb(OLD)-ARRAY['payload','provider_observation','replay_available'])
THEN RETURN NEW; END IF;
"""
GUARDS = {
    "guard_task": TASK_REDACTION,
    "guard_step": STEP_REDACTION,
    "guard_step_payload": STEP_REDACTION,
    "guard_call": CALL_REDACTION,
    "guard_provider_observation": CALL_REDACTION,
}


def upgrade() -> None:
    bind = op.get_bind()
    for name, exception in GUARDS.items():
        definition = str(
            bind.scalar(
                text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": name + "()"}
            )
        )
        backup = definition.replace("public." + name + "(", "public." + name + "_before_purge(", 1)
        op.execute(backup)
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}_before_purge() FROM PUBLIC")
        op.execute(definition.replace("BEGIN", "BEGIN\n" + exception, 1))
    op.execute("""
        ALTER TABLE external_calls ADD COLUMN replay_available boolean NOT NULL DEFAULT true;
        ALTER TABLE external_calls ADD CONSTRAINT replay_has_exact_payload CHECK(
            (replay_available AND octet_length(payload)>0) OR
            (NOT replay_available AND payload=''::bytea));
        ALTER TABLE idempotency_records DROP CONSTRAINT idempotency_records_response_status_check;
        ALTER TABLE idempotency_records ADD CONSTRAINT idempotency_records_response_status_check
            CHECK(response_status IN(200,202,410));
        CREATE FUNCTION purge_task_contents(target uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        DECLARE t public.tasks; owner uuid; moment timestamptz; changed integer=0;
                commands integer=0; reclaimed bigint;
        BEGIN
            PERFORM 1 FROM public.platform_control WHERE id=1 FOR UPDATE;
            SELECT principal_id INTO owner FROM public.tasks WHERE id=target;
            IF owner IS NULL THEN RETURN jsonb_build_object('tasks',0,'responses',0); END IF;
            PERFORM 1 FROM public.principals WHERE id=owner FOR UPDATE;
            PERFORM 1 FROM public.provider_admission WHERE id=1 FOR UPDATE;
            SELECT * INTO t FROM public.tasks WHERE id=target FOR UPDATE;
            PERFORM 1 FROM public.steps WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.attempts WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.external_calls WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.call_costs WHERE call_id IN
                (SELECT id FROM public.external_calls WHERE task_id=target)
                ORDER BY call_id FOR UPDATE;
            moment=clock_timestamp();
            IF t.purged_at IS NULL AND t.state IN('succeeded','failed','cancelled','timed_out')
                AND t.terminal_at<=moment-interval '30 days' THEN
                reclaimed=greatest(t.storage_bytes-1024,0);
                UPDATE public.tasks SET input='{}',result=NULL,purged_at=moment,
                    storage_bytes=storage_bytes-reclaimed,
                    oldest_retained_sequence=event_sequence+1 WHERE id=target;
                UPDATE public.steps SET result=NULL,protocol_items=NULL,payload=NULL
                    WHERE task_id=target;
                UPDATE public.external_calls SET payload=''::bytea,provider_observation=NULL,
                    replay_available=false WHERE task_id=target;
                DELETE FROM public.task_events WHERE task_id=target;
                INSERT INTO public.task_tombstones(task_id,principal_id,purged_at)
                    VALUES(target,owner,moment) ON CONFLICT(task_id) DO NOTHING;
                UPDATE public.principals SET storage_bytes=storage_bytes-reclaimed WHERE id=owner;
                UPDATE public.platform_control SET storage_bytes=storage_bytes-reclaimed WHERE id=1;
                changed=1;
            END IF;
            UPDATE public.idempotency_records SET response_body='',response_status=410,
                response_purged_at=moment WHERE task_id=target AND response_purged_at IS NULL
                AND created_at<=moment-interval '30 days';
            GET DIAGNOSTICS commands=ROW_COUNT;
            RETURN jsonb_build_object('tasks',changed,'responses',commands);
        END $$;
        REVOKE ALL ON FUNCTION purge_task_contents(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION purge_task_contents(uuid) TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION purge_task_contents(uuid)")
    bind = op.get_bind()
    for name in GUARDS:
        definition = str(
            bind.scalar(
                text("SELECT pg_get_functiondef(to_regprocedure(:name))"),
                {"name": name + "_before_purge()"},
            )
        )
        op.execute(
            definition.replace("public." + name + "_before_purge(", "public." + name + "(", 1)
        )
        op.execute(f"DROP FUNCTION public.{name}_before_purge()")
    # A redacted response/payload cannot be reconstructed by a downgrade.
    op.execute("""
        ALTER TABLE external_calls DROP CONSTRAINT replay_has_exact_payload;
        ALTER TABLE external_calls DROP COLUMN replay_available;
    """)
