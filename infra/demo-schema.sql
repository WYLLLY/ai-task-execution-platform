CREATE TABLE IF NOT EXISTS report_effects (
    owner uuid NOT NULL, operation uuid NOT NULL, effect_id uuid NOT NULL UNIQUE,
    payload_hash varchar(64) NOT NULL, payload bytea NOT NULL CHECK(octet_length(payload)<=65536),
    response text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner,operation)
);
CREATE TABLE IF NOT EXISTS invocation_journal (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transmission uuid NOT NULL, owner uuid NOT NULL, operation uuid NOT NULL,
    payload_hash varchar(64) NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON report_effects,invocation_journal FROM PUBLIC;
GRANT SELECT,INSERT ON report_effects,invocation_journal TO atp_demo;
GRANT USAGE ON SEQUENCE invocation_journal_sequence_seq TO atp_demo;
