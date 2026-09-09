-- Create Chimely's database and login idempotently before the service starts.
-- psql quotes the secret as a SQL literal; it is never written into the SQL file.
\getenv chimely_password CHIMELY_POSTGRES_PASSWORD
SELECT pg_advisory_lock(1684371046, 1);
SELECT 'CREATE ROLE chimely LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chimely')
\gexec
-- Reconcile an existing login when Compose's configured password changes.
SELECT format('ALTER ROLE chimely PASSWORD %L', :'chimely_password')
\gexec
SELECT 'CREATE DATABASE chimely OWNER chimely'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'chimely')
\gexec
REVOKE ALL ON DATABASE chimely FROM PUBLIC;
SELECT pg_advisory_unlock(1684371046, 1);
