#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

echo "==> Dropping public schema (wipe everything)"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO public;
SQL

echo "==> Applying schema"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/000_reset_v2.sql

echo "==> Done. Tables:"
psql "$DATABASE_URL" -c "\dt"
