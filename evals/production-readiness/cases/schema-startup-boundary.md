# Schema Startup Boundary Cases

| eval_id | case_id | rule_refs | input_fixture | expected_output | tolerance | auto_checkable | expected_red |
|---|---|---|---|---|---|---|---|
| E-PR-001 | C-PR-003 | R-PR-003 | production config plus mutation spies | no mutation call; verifier called once | none | yes | yes |
| E-PR-002 | C-PR-004 | R-PR-002, R-PR-003 | fresh database without Alembic version | fatal `SchemaRevisionMismatch` | message may vary only in ordering | yes | yes |
| E-PR-003 | C-PR-003 | R-PR-002, R-PR-003 | matching current and repository heads | verified state returned | revision values exact | yes | yes |
| E-PR-004 | C-PR-005 | R-PR-004 | development config plus call spies | create and legacy repair each called once | none | yes | yes |
| E-PR-005 | C-PR-004 | R-PR-005 | mismatch containing a sentinel DSN elsewhere | error includes heads and command, excludes sentinel | none | yes | yes |
