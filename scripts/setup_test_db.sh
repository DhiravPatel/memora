#!/usr/bin/env bash
# Create a throwaway database with pgvector for the e2e suite.
#
#   ./scripts/setup_test_db.sh
#   TEST_DATABASE_URL=postgresql+asyncpg://localhost/memory_test pytest tests/e2e
set -euo pipefail

DB_NAME="${1:-memory_test}"

createdb "$DB_NAME" 2>/dev/null || echo "database $DB_NAME already exists"
psql -q -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector" || {
  echo "pgvector is not available for this PostgreSQL installation." >&2
  echo "Install it first (e.g. 'brew install pgvector' or the postgresql-NN-pgvector package)." >&2
  exit 1
}

echo "Ready: TEST_DATABASE_URL=postgresql+asyncpg://localhost/$DB_NAME"
