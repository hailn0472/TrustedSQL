# TrustedSQL Database Bootstrap

`script.sql` is a schema-only PostgreSQL bootstrap script for the core
TrustedSQL academic database used by the runtime and evaluator.

It intentionally excludes:

- real or synthetic table rows;
- API keys, credentials, and connection strings;
- owner and grant statements tied to a local PostgreSQL/Supabase instance;
- Supabase memory tables and pgvector extension internals.

To create an empty database schema:

```powershell
psql "$env:TRUSTEDSQL_DATABASE_URL" -f database/script.sql
```

After loading the schema, import the approved benchmark database snapshot or
synthetic seed data separately. The runtime still reads connection details from
`TRUSTEDSQL_DATABASE_URL` or `DATABASE_URL`.

## Benchmark Snapshot Requirement

Full experiment reproduction requires the same database state used by the
paper runs. Utility metrics such as execution exact match and soft result F1
depend on table rows, not only on schema.

If the benchmark rows can be public, place the seed under:

```text
database/seed/benchmark_seed.sql
database/SHA256SUMS
```

and restore it after `script.sql`:

```powershell
psql "$env:TRUSTEDSQL_DATABASE_URL" -f database/script.sql
psql "$env:TRUSTEDSQL_DATABASE_URL" -f database/seed/benchmark_seed.sql
```

If the rows cannot be public, publish the snapshot as a supplementary artifact
and record at least:

- stable archive URL or DOI;
- SHA-256 checksum;
- PostgreSQL version;
- snapshot date;
- restore command;
- database role used by runtime evaluation.

The runtime database role should be granted only the permissions needed for
evaluation queries. X1 also runs generated SQL inside a read-only transaction
as a defense-in-depth execution barrier.
