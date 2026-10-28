-- Compatible forward addition for the independent demo database. No runtime role gains erasure.
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='atp_demo_retention') THEN
    CREATE ROLE atp_demo_retention NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.demo_retention_decisions (
  owner uuid NOT NULL, operation uuid NOT NULL, evidence_id text NOT NULL,
  decision_hash varchar(64) NOT NULL, terminal_at timestamptz NOT NULL,
  proof_sha256 varchar(64) NOT NULL, reason text NOT NULL, purged boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(owner,operation,evidence_id)
);
REVOKE ALL ON public.demo_retention_decisions FROM PUBLIC,atp_demo;
GRANT SELECT ON public.demo_retention_decisions TO atp_demo_retention;
GRANT USAGE ON SCHEMA public TO atp_demo_retention;
CREATE OR REPLACE FUNCTION public.purge_demo_content(proof jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET TimeZone='UTC' AS $$
DECLARE
  r public.report_effects%ROWTYPE;
  previous public.demo_retention_decisions%ROWTYPE;
  owner_id uuid; operation_id uuid; ended timestamptz; fingerprint text; erased boolean;
BEGIN
  IF jsonb_typeof(proof)<>'object' OR
     (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(proof) key) IS DISTINCT FROM
     ARRAY['evidence_id','operation','owner','payload_sha256','proof_sha256','reason','terminal_at'] OR
     EXISTS(SELECT FROM jsonb_each(proof) WHERE jsonb_typeof(value)<>'string') OR
     proof->>'evidence_id' !~ '^[A-Za-z0-9:_-]{1,128}$' OR
     proof->>'payload_sha256' !~ '^[0-9a-f]{64}$' OR
     proof->>'proof_sha256' !~ '^[0-9a-f]{64}$' OR
     proof->>'terminal_at' !~ '(Z|\+00:00)$' OR
     length(btrim(proof->>'reason')) NOT BETWEEN 1 AND 240 THEN
    RAISE EXCEPTION 'Invalid retention evidence' USING ERRCODE='22023';
  END IF;
  owner_id := (proof->>'owner')::uuid; operation_id := (proof->>'operation')::uuid;
  ended := (proof->>'terminal_at')::timestamptz;
  fingerprint := encode(sha256(convert_to(proof::text,'UTF8')),'hex');
  SELECT * INTO r FROM public.report_effects WHERE owner=owner_id AND operation=operation_id
    FOR UPDATE;
  IF NOT FOUND OR r.payload_hash<>proof->>'payload_sha256' THEN
    RAISE EXCEPTION 'Retention identity mismatch' USING ERRCODE='22023';
  END IF;
  SELECT * INTO previous FROM public.demo_retention_decisions WHERE owner=owner_id
    AND operation=operation_id AND evidence_id=proof->>'evidence_id';
  IF FOUND THEN
    IF previous.decision_hash<>fingerprint THEN
      RAISE EXCEPTION 'Retention evidence conflict' USING ERRCODE='22023';
    END IF;
    RETURN previous.purged;
  END IF;
  -- Fresh time after the operation lock; never infer termination from receipt time.
  IF NOT isfinite(ended) OR ended>clock_timestamp()-interval '30 days' THEN
    RAISE EXCEPTION 'Retention clock not reached' USING ERRCODE='22023';
  END IF;
  erased := octet_length(r.payload)>0;
  INSERT INTO public.demo_retention_decisions
    (owner,operation,evidence_id,decision_hash,terminal_at,proof_sha256,reason,purged)
    VALUES(owner_id,operation_id,proof->>'evidence_id',fingerprint,ended,
           proof->>'proof_sha256',proof->>'reason',erased);
  IF erased THEN
    UPDATE public.report_effects SET payload=''::bytea WHERE owner=owner_id AND operation=operation_id;
  END IF;
  RETURN erased;
END $$;
REVOKE ALL ON FUNCTION public.purge_demo_content(jsonb) FROM PUBLIC,atp_demo;
GRANT EXECUTE ON FUNCTION public.purge_demo_content(jsonb) TO atp_demo_retention;
CREATE OR REPLACE FUNCTION public.guard_demo_content() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog SET TimeZone='UTC' AS $$
BEGIN
  IF NEW.payload<>''::bytea OR
     (to_jsonb(NEW)-'payload') IS DISTINCT FROM (to_jsonb(OLD)-'payload') OR
     NOT EXISTS(SELECT FROM public.demo_retention_decisions d WHERE d.owner=OLD.owner
       AND d.operation=OLD.operation AND d.purged AND
       d.terminal_at<=clock_timestamp()-interval '30 days') THEN
    RAISE EXCEPTION 'Immutable report effect' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_demo_content ON public.report_effects;
CREATE TRIGGER guard_demo_content BEFORE UPDATE ON public.report_effects
  FOR EACH ROW EXECUTE FUNCTION public.guard_demo_content();
CREATE OR REPLACE FUNCTION public.immutable_demo_retention() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog SET TimeZone='UTC' AS $$
BEGIN RAISE EXCEPTION 'Immutable retention evidence' USING ERRCODE='23514'; END $$;
DROP TRIGGER IF EXISTS immutable_demo_retention ON public.demo_retention_decisions;
CREATE TRIGGER immutable_demo_retention BEFORE UPDATE OR DELETE ON public.demo_retention_decisions
  FOR EACH ROW EXECUTE FUNCTION public.immutable_demo_retention();
COMMIT;
