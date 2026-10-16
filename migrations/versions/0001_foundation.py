"""M1 identity, operator control and append-only administrative audit."""

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE principals (
            id uuid PRIMARY KEY,
            name varchar(100) NOT NULL CHECK (length(btrim(name)) > 0),
            active boolean NOT NULL DEFAULT true,
            permission_version bigint NOT NULL DEFAULT 1 CHECK (permission_version > 0),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE api_keys (
            id uuid PRIMARY KEY,
            principal_id uuid NOT NULL REFERENCES principals(id),
            digest varchar(64) NOT NULL UNIQUE CHECK (digest ~ '^[0-9a-f]{64}$'),
            permission_version bigint NOT NULL DEFAULT 1 CHECK (permission_version > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz,
            revoked_at timestamptz,
            CHECK (expires_at IS NULL OR expires_at > created_at)
        );
        CREATE INDEX ix_api_keys_principal_id ON api_keys(principal_id);
        CREATE TABLE platform_control (
            id integer PRIMARY KEY CHECK (id = 1),
            execution_epoch uuid NOT NULL,
            dispatch_open boolean NOT NULL DEFAULT false,
            admission_open boolean NOT NULL DEFAULT false,
            version bigint NOT NULL DEFAULT 1 CHECK (version > 0)
        );
        CREATE TABLE audit_events (
            id uuid PRIMARY KEY,
            action varchar(40) NOT NULL CHECK (action IN (
                'principal.created', 'principal.disabled', 'key.created',
                'key.revoked', 'platform.initialized'
            )),
            principal_id uuid REFERENCES principals(id),
            subject_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
        GRANT SELECT ON principals, api_keys, platform_control, alembic_version
            TO atp_api, atp_worker;
        GRANT SELECT, INSERT, UPDATE ON principals, api_keys, platform_control TO atp_admin;
        GRANT SELECT, INSERT ON audit_events TO atp_admin;
        GRANT SELECT ON alembic_version TO atp_admin;
    """)


def downgrade() -> None:
    # Explicit operator action only; no startup or automatic deployment downgrade.
    op.execute("DROP TABLE audit_events, api_keys, principals, platform_control")
