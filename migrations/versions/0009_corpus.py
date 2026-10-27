"""Explicit corpus redaction preserves version identity and active references."""

from alembic import op
from sqlalchemy import text

revision = "0009_corpus"
down_revision = "0008_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    definition = str(
        op.get_bind().scalar(text("SELECT pg_get_functiondef('guard_document()'::regprocedure)"))
    )
    op.execute(
        definition.replace("public.guard_document(", "public.guard_document_before_purge(", 1)
    )
    op.execute("REVOKE ALL ON FUNCTION guard_document_before_purge() FROM PUBLIC")
    exception = """
    IF TG_OP='UPDATE' AND current_user='atp_migrator' AND OLD.purged_at IS NULL
        AND NEW.purged_at IS NOT NULL AND NEW.content='' AND NOT NEW.readable
        AND (to_jsonb(NEW)-ARRAY['content','readable','purged_at'])
            =(to_jsonb(OLD)-ARRAY['content','readable','purged_at'])
    THEN RETURN NEW; END IF;
    """
    op.execute(definition.replace("BEGIN", "BEGIN\n" + exception, 1))
    op.execute("""
        ALTER TABLE audit_events DROP CONSTRAINT audit_events_action_check;
        ALTER TABLE audit_events ADD CONSTRAINT audit_events_action_check CHECK(action IN
            ('principal.created','principal.disabled','key.created','key.revoked',
             'platform.initialized','platform.admission','workflow.availability',
             'provider.configured','accounting.settlement','accounting.acknowledgement','corpus.purged'));
        ALTER TABLE documents ADD COLUMN purged_at timestamptz;
        ALTER TABLE documents DROP CONSTRAINT documents_content_check;
        ALTER TABLE documents ADD CONSTRAINT documents_content_check CHECK(
            (purged_at IS NULL AND octet_length(content) BETWEEN 1 AND 1048576) OR
            (purged_at IS NOT NULL AND content='' AND NOT readable));
        CREATE FUNCTION delete_corpus_version(owner uuid, doc uuid, v integer) RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        DECLARE d public.documents; reclaimed bigint;
        BEGIN
            PERFORM 1 FROM public.platform_control WHERE id=1 FOR UPDATE;
            PERFORM 1 FROM public.principals WHERE id=owner FOR UPDATE;
            SELECT * INTO d FROM public.documents WHERE id=doc AND version=v
                AND principal_id=owner FOR UPDATE;
            IF NOT FOUND THEN RETURN 'missing'; END IF;
            IF d.purged_at IS NOT NULL THEN RETURN 'retained'; END IF;
            IF EXISTS(SELECT 1 FROM public.tasks t WHERE t.principal_id=owner
                AND t.state IN('queued','running','suspended') AND
                t.input->'documents' @> jsonb_build_array(jsonb_build_object(
                    'id',doc::text,'version',v))) THEN RETURN 'active'; END IF;
            reclaimed=octet_length(d.content);
            UPDATE public.documents SET content='',readable=false,purged_at=clock_timestamp()
                WHERE id=doc AND version=v;
            UPDATE public.principals SET storage_bytes=storage_bytes-reclaimed WHERE id=owner;
            UPDATE public.platform_control SET storage_bytes=storage_bytes-reclaimed WHERE id=1;
            INSERT INTO public.audit_events(id,action,principal_id,subject_id)
                VALUES(gen_random_uuid(),'corpus.purged',owner,doc);
            RETURN 'deleted';
        END $$;
        REVOKE ALL ON FUNCTION delete_corpus_version(uuid,uuid,integer) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION delete_corpus_version(uuid,uuid,integer) TO atp_admin;
    """)


def downgrade() -> None:
    op.execute("DELETE FROM audit_events WHERE action='corpus.purged'")
    # Keep the redacted-row-compatible CHECK; erased content cannot be reconstructed.
    op.execute("DROP FUNCTION delete_corpus_version(uuid,uuid,integer)")
    definition = str(
        op.get_bind().scalar(
            text("SELECT pg_get_functiondef('guard_document_before_purge()'::regprocedure)")
        )
    )
    op.execute(
        definition.replace("public.guard_document_before_purge(", "public.guard_document(", 1)
    )
    op.execute("DROP FUNCTION guard_document_before_purge()")
    op.execute("ALTER TABLE documents DROP CONSTRAINT documents_content_check")
    op.execute("ALTER TABLE documents ADD CHECK(octet_length(content) BETWEEN 0 AND 1048576)")
    op.execute("ALTER TABLE documents DROP COLUMN purged_at")
