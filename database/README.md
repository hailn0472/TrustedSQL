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
