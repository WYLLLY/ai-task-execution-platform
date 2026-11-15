"""Read-only parity check for both PostgreSQL databases during a drained cutover."""

import argparse
import hashlib
import json
import re
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

RELATIONS = """
    SELECT c.relname, c.relkind::text
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON c.relnamespace = n.oid
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'S')
    ORDER BY c.relname COLLATE "C"
"""
REQUIRED = {
    "platform": {"alembic_version", "platform_control", "tasks", "api_keys", "external_calls"},
    "demo": {"report_effects", "invocation_journal"},
}


@dataclass(frozen=True, slots=True)
class Snapshot:
    cluster: str
    major: int
    relations: dict[str, str]


def table_digest(cursor: psycopg.Cursor[TupleRow], relation: sql.Identifier) -> str:
    query = sql.SQL(
        """COPY (
            SELECT sha256(convert_to(to_jsonb(entry)::text, 'UTF8')) FROM {} AS entry ORDER BY 1
        ) TO STDOUT (ENCODING 'UTF8')"""
    ).format(relation)
    digest = hashlib.sha256()
    with cursor.copy(query) as rows:
        for block in rows:
            digest.update(block)
    return digest.hexdigest()


def capture(service: str, application: str) -> Snapshot:
    with (
        psycopg.connect(service=service, connect_timeout=5) as connection,
        connection.cursor() as c,
    ):
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        c.execute("SET LOCAL statement_timeout = '15min'")
        c.execute("SET LOCAL TIME ZONE 'UTC'")
        c.execute("SET LOCAL DateStyle = 'ISO, YMD'")
        c.execute("SET LOCAL IntervalStyle = 'iso_8601'")
        c.execute("SET LOCAL bytea_output = 'hex'")
        c.execute("SET LOCAL extra_float_digits = 3")
        c.execute("SET LOCAL search_path = pg_catalog")
        c.execute("SET LOCAL row_security = off")
        c.execute("SELECT system_identifier::text FROM pg_control_system()")
        identity = c.fetchone()
        if not identity:
            raise ValueError("Cannot determine PostgreSQL cluster identity")
        c.execute(RELATIONS)
        names = c.fetchall()
        if not REQUIRED[application].issubset(name for name, kind in names if kind == "r"):
            raise ValueError(f"{application}: expected application tables missing")
        if application == "platform":
            c.execute(
                """
                SELECT (SELECT NOT admission_open AND NOT dispatch_open
                            FROM public.platform_control WHERE id=1) IS TRUE
                    AND NOT EXISTS (SELECT FROM public.external_calls WHERE local_active)
                    AND NOT EXISTS (SELECT FROM public.attempts WHERE state='active')
                """
            )
            if c.fetchone() != (True,):
                raise ValueError("ATP admission and dispatch must be closed and execution drained")
        digests: dict[str, str] = {}
        for name, kind in names:
            relation = sql.Identifier("public", name)
            if kind == "S":
                c.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(relation))
                digests[name] = repr(c.fetchone())
            else:
                digests[name] = table_digest(c, relation)
        return Snapshot(identity[0], connection.info.server_version // 10000, digests)


def verify(services: dict[str, str]) -> dict[str, object]:
    snapshots = {
        name: capture(service, name.split("_", 1)[1]) for name, service in services.items()
    }
    if len({snapshot.cluster for snapshot in snapshots.values()}) != 4:
        raise ValueError("The four PostgreSQL connections must use distinct clusters")
    if any(snapshot.major != 17 for snapshot in snapshots.values()):
        raise ValueError("Expected PostgreSQL 17 on all four clusters")
    for application in REQUIRED:
        source = snapshots[f"source_{application}"]
        restored = snapshots[f"restored_{application}"]
        if source.relations != restored.relations:
            raise ValueError(f"{application}: restored data or sequence state differs")
    return {
        "status": "PASS",
        "platform_relations": len(snapshots["restored_platform"].relations),
        "demo_relations": len(snapshots["restored_demo"].relations),
    }


def service_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
        raise argparse.ArgumentTypeError("Expected a libpq service name")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for boundary in ("source", "restored"):
        for application in REQUIRED:
            parser.add_argument(f"--{boundary}-{application}", required=True, type=service_name)
    try:
        result = verify(vars(parser.parse_args()))
    except psycopg.Error:
        parser.exit(1, "Cutover verification could not read all four databases.\n")
    except ValueError as error:
        parser.exit(1, f"Cutover verification failed. {error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
