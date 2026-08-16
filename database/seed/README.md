# Benchmark Seed Data

Place the public benchmark seed file here if the paper database rows can be
distributed inside the source repository.

Expected file:

```text
database/seed/benchmark_seed.sql
```

If the rows are distributed as a supplementary artifact instead, keep this
folder as a pointer and complete `database/benchmark_snapshot_manifest.example.json`
with the archive URL or DOI, SHA-256 checksum, PostgreSQL version, snapshot
date, and restore command.
