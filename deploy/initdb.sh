#!/bin/sh
set -eu
# The runtime role has no schema ownership or superuser privileges.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 --set=app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE zc_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE clearledger TO zc_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
