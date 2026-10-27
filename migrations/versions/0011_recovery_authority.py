"""Explicit audited reassociation after restoration; original remote call identities stay fixed."""

from alembic import op
from sqlalchemy import text

revision = "0011_recovery_authority"
down_revision = "0010_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    definition = str(
        op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_task()'::regprocedure)"))
    )
    op.execute(definition.replace("public.guard_task(", "public.guard_task_before_recovery(", 1))
    op.execute("REVOKE ALL ON FUNCTION guard_task_before_recovery() FROM PUBLIC")
    exception = """
    IF current_user='atp_migrator' THEN
    IF OLD.restore_uncertain
        AND OLD.state='suspended' AND OLD.reason='restore_quarantine'
        AND NEW.state IN('queued','suspended')
        AND NEW.reason IS NOT DISTINCT FROM
            (CASE WHEN NEW.state='suspended' THEN 'external_unknown'::text ELSE NULL END)
        AND NEW.task_version=OLD.task_version+1 AND NEW.event_sequence=OLD.event_sequence+1
        AND (to_jsonb(NEW)-ARRAY['key_id','execution_epoch','state','reason',
                                'task_version','event_sequence'])
            =(to_jsonb(OLD)-ARRAY['key_id','execution_epoch','state','reason',
                                'task_version','event_sequence'])
        AND EXISTS(SELECT 1 FROM public.restore_decisions d WHERE d.epoch=NEW.execution_epoch
            AND d.kind='release' AND d.details->>'task_id'=OLD.id::text
            AND d.details->>'key_id'=NEW.key_id::text
            AND (d.details->>'expected_task_version')::bigint=OLD.task_version
            AND (d.details->>'no_unrecorded_effect')::boolean IS TRUE)
    THEN RETURN NEW; END IF;
    END IF;
    """
    op.execute(definition.replace("BEGIN", "BEGIN\n" + exception, 1))
    op.execute("""
        CREATE FUNCTION lock_restore_state(target uuid, current_key uuid) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        DECLARE owner uuid;
        BEGIN
            SELECT principal_id INTO owner FROM public.tasks WHERE id=target;
            PERFORM 1 FROM public.platform_control WHERE id=1 FOR UPDATE;
            PERFORM 1 FROM public.principals WHERE id=owner FOR UPDATE;
            PERFORM 1 FROM public.api_keys WHERE id=current_key FOR UPDATE;
            PERFORM 1 FROM public.provider_admission WHERE id=1 FOR UPDATE;
            PERFORM 1 FROM public.tasks WHERE id=target FOR UPDATE;
            PERFORM 1 FROM public.steps WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.attempts WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.external_calls WHERE task_id=target ORDER BY id FOR UPDATE;
            PERFORM 1 FROM public.call_costs WHERE call_id IN
                (SELECT id FROM public.external_calls WHERE task_id=target)
                ORDER BY call_id FOR UPDATE;
        END $$;
        REVOKE ALL ON FUNCTION lock_restore_state(uuid,uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION lock_restore_state(uuid,uuid) TO atp_admin;
        CREATE FUNCTION associate_restored_task(target uuid, current_key uuid, epoch uuid,
            resolution boolean) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,public AS $$
        BEGIN
            PERFORM public.lock_restore_state(target,current_key);
            IF NOT EXISTS(SELECT 1 FROM public.platform_control WHERE execution_epoch=epoch
                AND dispatch_open AND (recovery->>'bounded')::boolean IS TRUE) OR
                NOT EXISTS(SELECT 1 FROM public.tasks t JOIN public.api_keys k
                    ON k.id=current_key AND k.principal_id=t.principal_id
                    JOIN public.principals p ON p.id=t.principal_id
                    WHERE t.id=target AND p.active AND k.revoked_at IS NULL
                    AND (k.expires_at IS NULL OR k.expires_at>clock_timestamp())
                    AND t.deadline_at>clock_timestamp()) THEN
                RAISE EXCEPTION 'Current restore authorization unavailable' USING ERRCODE='23514';
            END IF;
            UPDATE public.tasks SET key_id=current_key,execution_epoch=epoch,
                state=CASE WHEN resolution THEN 'suspended' ELSE 'queued' END,
                reason=CASE WHEN resolution THEN 'external_unknown' ELSE NULL END,
                task_version=task_version+1,event_sequence=event_sequence+1 WHERE id=target;
        END $$;
        REVOKE ALL ON FUNCTION associate_restored_task(uuid,uuid,uuid,boolean) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION associate_restored_task(uuid,uuid,uuid,boolean) TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION associate_restored_task(uuid,uuid,uuid,boolean)")
    op.execute("DROP FUNCTION IF EXISTS lock_restore_state(uuid,uuid)")
    definition = str(
        op.get_bind().scalar(
            text("SELECT pg_get_functiondef('guard_task_before_recovery()'::regprocedure)")
        )
    )
    op.execute(definition.replace("public.guard_task_before_recovery(", "public.guard_task(", 1))
    op.execute("DROP FUNCTION guard_task_before_recovery()")
