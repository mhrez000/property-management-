#!/bin/sh
# Runs once on first boot of the Postgres container (docker-entrypoint-initdb.d).
# Creates the application role as a NON-superuser: RLS policies do not bind
# superusers, so the app must never connect as one.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<'SQL'
CREATE ROLE propman LOGIN PASSWORD 'propman' NOSUPERUSER CREATEDB;
CREATE DATABASE propman OWNER propman;
SQL
