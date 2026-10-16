#!/bin/sh
set -eu
# This runs once on a fresh dedicated cluster, before application migrations.
# Password values pass through the psql environment, never shell arguments or logs.
ATP_API_PASSWORD=$(cat /run/secrets/api_password)
ATP_WORKER_PASSWORD=$(cat /run/secrets/worker_password)
ATP_ADMIN_PASSWORD=$(cat /run/secrets/admin_password)
ATP_MIGRATOR_PASSWORD=$(cat /run/secrets/migrator_password)
export ATP_API_PASSWORD ATP_WORKER_PASSWORD ATP_ADMIN_PASSWORD ATP_MIGRATOR_PASSWORD
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv api_password ATP_API_PASSWORD
\getenv worker_password ATP_WORKER_PASSWORD
\getenv admin_password ATP_ADMIN_PASSWORD
\getenv migrator_password ATP_MIGRATOR_PASSWORD
CREATE ROLE atp_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'api_password';
CREATE ROLE atp_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'worker_password';
CREATE ROLE atp_admin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'admin_password';
CREATE ROLE atp_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'migrator_password';
REVOKE ALL ON DATABASE atp FROM PUBLIC;
GRANT CONNECT ON DATABASE atp TO atp_api, atp_worker, atp_admin, atp_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO atp_migrator;
GRANT USAGE ON SCHEMA public TO atp_api, atp_worker, atp_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE atp_migrator REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE atp_migrator REVOKE ALL ON TABLES FROM PUBLIC;
SQL
