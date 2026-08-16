# TrustedSQL Runtime Architecture

This document describes the current runtime implemented in `src/trustedsql/`.

## Scientific Boundary

Runtime receives only the current NLQ, role, user id, and runtime-generated conversation history. It never receives ground-truth SQL, expected results, dataset labels, attack tags, training labels, review fields, or evaluator evidence.

The full pipeline is:

```text
C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1
```

Every module returns `ALLOW`, `DENY`, or `ERROR`. A `DENY` or `ERROR` stops the current turn.

## Runtime History

Each subsequent turn receives the complete prior runtime history:

```json
{
  "turn_id": 1,
  "nlq": "...",
  "decision": "ALLOW",
  "raw_sql": "SELECT ...",
  "final_sql": "SELECT ...",
  "executed": true,
  "execution_result_json": []
}
```

M3 and M6 receive all available turns. History is runtime data, not trusted instruction text.

## C0 Runtime Context Builder

C0 loads canonical schema, compact schema, policy index, role matrix, role/user identity, and conversation history. It does not make a security decision.

## M1 Prompt Integrity Guard

M1 detects direct prompt manipulation and policy-bypass language using deterministic signals and an optional structured LLM classifier. It does not evaluate table, column, or row authorization.

## M2 Intent-GNN Risk Guard

M2 evaluates conversational risk using the promoted GNN model and runtime graph state. Its decision logic and model assets are independent from M3-M6. An advisory downstream hint may be logged and supplied to M3, but it cannot modify the role-authorized schema used by M6.

## M3 Policy-Grounded Resource Planner

M3 is an LLM resource planner, not a SQL planner. Its input is:

```json
{
  "role": "student",
  "user_id": 40,
  "current_nlq": "Filter the report to passed enrollments only.",
  "history": [
    {
      "turn_id": 1,
      "nlq": "...",
      "decision": "ALLOW",
      "final_sql": "SELECT ...",
      "execution_result_json": []
    }
  ],
  "policy_summary": "...",
  "compact_schema": "...",
  "m2_hint": {}
}
```

M3 returns a Pydantic-validated `ResourcePlan`:

```json
{
  "intent": "Refine the previous enrollment report",
  "policy_refs": ["S03"],
  "requested_resources": [
    {"table": "enrollments", "columns": ["status"]}
  ],
  "scope_type": "SELF",
  "target_resource_table": "enrollments",
  "target_identity_predicates": [],
  "query_filter_predicates": [
    {"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}
  ]
}
```

`target_identity_predicates` identify a resource whose relationship to the requester must be proven. `query_filter_predicates` only refine the requested answer. M3 does not output SQL, join paths, structural fields, projections, computations, or a schema scope.

## M4 Table/Column Access Validator

M4 is deterministic. It validates:

- policy references against the current role;
- requested tables and columns against canonical DDL and role matrix;
- target and query predicate columns against the same sources.

M4 derives the row filter and scope anchor from the selected canonical policy. It does not trust an LLM-provided join path or scope anchor. An explicit disallowed table/column produces `DENY`.

M4 returns a `ResourceContract`:

```json
{
  "policy_refs": ["S03"],
  "scope_type": "SELF",
  "target_resource_table": "enrollments",
  "scope_anchor_table": "enrollments",
  "row_filter": "enrollments.student_id = @user_id",
  "target_identity_predicates": [],
  "query_filter_predicates": [
    {"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}
  ],
  "requires_db_proof": false
}
```

## M5 Row-Scope Proof Verifier

M5 is deterministic and runs before SQL generation.

- `ALL`: allow without a DB proof.
- Scoped request without an external target: allow with the canonical current-user binding.
- Scoped request with an external target: compile and execute a parameterized `SELECT EXISTS`.
- Unsupported proof, DB error, or target outside scope: deny.

Only `target_identity_predicates` participate in `SELECT EXISTS`. Query-content filters such as status, grade threshold, category, ordering, or date do not establish authorization and are excluded from proof.

Join paths are policy-grounded: paths through tables named by the canonical row filter are preferred before path length is considered.

M5 returns `VerifiedAuthorization`:

```json
{
  "policy_refs": ["S03"],
  "scope_type": "SELF",
  "current_user_bindings": [
    {"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}
  ],
  "verified_targets": []
}
```

M5 remains a pre-generation gate. M7 does not re-check row-scope semantics.

## Role-Authorized Schema

M6 schema is generated in memory for the current role from two canonical sources:

```text
canonical DDL + compact schema examples + role_access_matrix -> role-authorized compact schema
```

It contains only role-allowed tables and columns. Coarse types, primary/unique/foreign-key tags, valid relationships, and whitelisted value examples are preserved. Foreign-key relationships are retained only when both endpoint tables and columns are permitted. Missing structural relationship columns cause a configuration error rather than implicit permission expansion.

No student/lecturer schema files or cross-run schema cache are created. Column examples are queried only for whitelisted identifier/categorical columns and illustrate value formats; they are not authorization evidence.

## M6 SQL Generator

M6 is an LLM Text-to-SQL generator. Its common input is:

```json
{
  "role": "student",
  "user_id": 40,
  "current_nlq": "...",
  "history": [...],
  "role_authorized_schema": "role-authorized compact schema text",
  "verified_authorization_context": {
    "scope_type": "SELF | ENROLLED | ASSIGNED | ALL",
    "mandatory_scope_predicates": [...],
    "verified_target_predicates": [...]
  }
}
```

M6 uses prior successful SQL/results to resolve conversational refinements. It preserves relevant projection and constraints for filter/add/sort/group/limit follow-ups unless the current request explicitly replaces them. It does not receive a field-level generation contract.

The final setting is `full_trustedsql`. M6 always receives a sanitized authorization guide from M5 containing `scope_type`, schema-validated `mandatory_scope_predicates`, and predicates whose external target proof succeeded. M3 resource plans, policy references, and proof SQL remain audit data and are not placed in the generation prompt.

## M7 SQL Conformance Validator

M7 preserves its existing decision boundary:

- parse with SQLGlot;
- require one SELECT statement;
- reject dangerous patterns and unresolved placeholders;
- deny role-disallowed tables and columns.

M7 does not enforce a generation contract, rewrite SQL, or validate row-scope semantics.

## X1 Executor

X1 executes the final SELECT through a database-enforced read-only transaction with statement timeout and row limit. Runtime output includes decision, SQL, execution result, module trace, latency, token usage, and error state.

## Evaluation

Evaluation is automatic and runs after runtime. Utility uses ST-EX, ST-Soft-F1, MT-Turn-EX, MT-Turn-Soft-F1, and MT-IEX. Multi-turn security uses Prefix-RS before conditional security analysis: each benign prefix turn is sufficient when all gold result facts appear in the runtime result after canonicalization, while aliases, column order, row order, extra columns, and extra rows are tolerated. Sequence ASR and Sequence Refusal Recall measure whether each malicious multi-turn sequence is allowed or refused at its decisive malicious request, independent of prefix quality. Conditional ASR, Conditional Refusal Recall, and Valid Secure Sequence Rate are computed against Prefix-RS-pass interactions. `ERROR` remains separate from `DENY` and is available in runtime diagnostics rather than reported as a conditional security metric.
