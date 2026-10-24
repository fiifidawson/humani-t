# Security controls and the tests that prove them

Every control below is enforced in code **and** has a test that fails if the
control is removed. A control without a test is listed under *Gaps*, not here.

References are to the requirements in [`WEB_APP_BRIEF.md`](../WEB_APP_BRIEF.md)
("brief rule n"), NIST SP 800-63B rev 4, and OWASP ASVS 5.0 **chapters**
(V3 Web Frontend, V6 Authentication, V7 Session Management, V8 Authorization,
V11 Cryptography, V13 Configuration, V16 Logging). This is a map of what is
built, not a claim of ASVS compliance: that needs an independent assessment.

Test paths are relative to `services/api/tests/`.

## Authentication (brief rule 6; NIST 800-63B; ASVS V6)

| Control | Where | Proven by |
| --- | --- | --- |
| Passwords ≥ 15 characters, no composition rules | `domain/passwords.py` | `test_passwords.py::TestPasswordPolicy` |
| Common, repeated and personal-detail passwords refused | `domain/passwords.py` | `test_passwords.py::TestPasswordPolicy` |
| Unicode passwords normalised (NFKC) before hashing | `domain/passwords.py` | `test_passwords.py::TestNormalisation` |
| Argon2id hashing, salted, upgraded at next login | `auth/argon2.py`, `application/accounts.py` | `test_passwords.py::TestArgon2PasswordHasher` |
| Same response and same work for unknown accounts | `application/accounts.py` (`login`) | `test_auth_api.py::TestLogin` (indistinguishable, still costs a verification) |
| Throttling per account, client and email; not lockout | `application/accounts.py`, `ratelimit/` | `test_auth_api.py::TestThrottling`, `test_ratelimit_contract.py` |
| Parallel guesses cannot exceed the limit | `application/accounts.py` (count before verify) | `test_concurrency_and_resources.py::TestThrottleUnderConcurrency` |
| Registration by invitation only; first admin from the CLI | `routes/admin.py`, `cli.py` | `test_auth_api.py::TestInvitations`, `test_cli.py::TestCreateAdmin` |
| Reset links: single use (also under races), 30 min, newest wins | `application/accounts.py`, `storage/` (`claim`) | `test_auth_api.py::TestPasswordReset`, `test_storage_contract.py::TestPostgresConcurrency` |
| Reset requests reveal nothing about the account | `application/accounts.py` | `test_auth_api.py::TestPasswordReset::test_requesting_a_reset_looks_the_same_for_any_email` |
| Tokens in emailed links travel in the URL fragment | `application/accounts.py` | `test_auth_api.py::TestPasswordReset::test_the_link_carries_the_token_in_the_fragment…` |
| Password change and reset notify the user by email | `application/accounts.py` | `TestChangePassword::test_the_user_is_told_by_email`, `TestPasswordReset::test_the_user_is_told…` |
| Passwords never accepted as CLI arguments | `cli.py` | `test_cli.py::TestCreateAdmin::test_the_password_is_never_accepted_as_an_argument` |

## Sessions (NIST AAL2; ASVS V7)

| Control | Where | Proven by |
| --- | --- | --- |
| 256-bit random tokens; only SHA-256 stored | `domain/sessions.py` | `test_sessions.py::TestTokens`, `test_auth_api.py::TestSessionCookie::test_a_session_token_is_never_stored_in_the_clear` |
| `__Host-` cookies: Secure, HttpOnly (session), SameSite | `api/session.py` | `test_auth_api.py::TestSessionCookie` |
| New token at every sign-in (no fixation) | `application/accounts.py` | `TestSessionCookie::test_signing_in_always_issues_a_new_token` |
| 1 h idle, 24 h absolute | `domain/sessions.py` | `test_sessions.py::TestSessionLifetime`, `test_auth_api.py::TestCurrentSession` |
| Logout ends the session on the server | `application/accounts.py` | `TestLogout::test_logout_ends_the_session_on_the_server_not_just_in_the_browser` |
| Users can list and revoke their sessions | `routes/account.py` | `test_auth_api.py::TestSessionManagement` |
| Revocation cannot be undone by a racing request | `storage/` (`touch`, `revoke` conditional) | `test_concurrency_and_resources.py::TestNoLostUpdates` |
| Password change and reset end every other session | `application/accounts.py` | `TestChangePassword`, `TestPasswordReset` |
| Sensitive actions need a sign-in within 15 min | `application/accounts.py` | `TestChangePassword::test_a_sign_in_older_than_15_minutes_must_be_confirmed_first` |

## Request integrity (ASVS V3, V13)

| Control | Where | Proven by |
| --- | --- | --- |
| Cross-site writes refused by `Origin` / `Sec-Fetch-Site` | `api/middleware.py` | `test_csrf.py::TestOriginCheck` |
| Per-session CSRF token required for every signed-in write | `api/session.py` | `test_csrf.py::TestSessionToken` |
| Only JSON bodies accepted | `api/middleware.py` | `test_csrf.py::TestBodyFormat` |
| Security headers on every response (CSP, HSTS, nosniff…) | `api/middleware.py` | `test_http_hardening.py::TestSecurityHeaders` |
| Errors as problem documents; never echo input or internals | `api/errors.py` | `test_http_hardening.py::TestProblemResponses` |
| Docs and `/info` absent in production | `main.py` | `test_http_hardening.py::TestProductionSurface` |
| Request bodies over 1 MiB refused before reading | `api/middleware.py` | `test_concurrency_and_resources.py::TestOverloadIsReportedNotCrashed` |
| Production refuses to start without its secrets | `settings.py` | `test_settings.py::TestProductionRequirements` |

## Authorization (brief rules 7–8; ASVS V8)

| Control | Where | Proven by |
| --- | --- | --- |
| Admin-only endpoints refuse other users (403) and anonymous (401) | `application/accounts.py` | `TestInvitations::test_only_admins_can_invite` |
| Another user's session is invisible (404, not 403) | `application/accounts.py` | `TestSessionManagement::test_another_users_session_cannot_be_revoked_or_even_seen` |
| Disabled accounts cannot sign in; their sessions end | `application/accounts.py` | `TestLogin::test_a_disabled_account_cannot_sign_in` |
| Layering: no route or use case imports a concrete provider | `pyproject.toml` (import-linter) | `uv run lint-imports` |

Project roles (owner, reviewer, viewer) arrive with projects (M10).

## Cryptography and data (brief rules 15, 17; ASVS V11)

| Control | Where | Proven by |
| --- | --- | --- |
| AES-256-GCM, random nonces, tamper detection | `crypto/aesgcm.py` | `test_encryption.py::TestAesGcmCipher` |
| Ciphertext bound to project, table, column and row | `domain/encryption.py` | `test_encryption.py::TestScopeEncryption` |
| Per-project data keys wrapped by an operator-held KEK | `crypto/local_key.py`, `application/encryption.py` | `test_encryption.py::TestLocalKeyProvider`, `TestScopeEncryption` |
| Deleting a project's key makes its data unreadable (crypto-shredding) | `application/encryption.py` | `TestScopeEncryption::test_shredded_scope_is_permanently_unreadable` |
| Shredding cannot be undone by a racing key rotation | `storage/` | `test_storage_contract.py::TestShreddingIsFinal` |
| KEK rotation keeps old data readable | `application/encryption.py` | `TestScopeEncryption::test_old_data_still_decrypts_after_kek_rotation` |

## Audit (brief rule 16; ASVS V16)

| Control | Where | Proven by |
| --- | --- | --- |
| Hash-chained events; edits, deletions, reordering detected | `domain/audit.py` | `test_audit.py::TestVerifyChain` |
| Concurrent writers cannot fork the chain | `storage/postgres/` (head lock) | `test_storage_contract.py::TestPostgresAuditGuarantees` |
| App role cannot `UPDATE`/`DELETE` events; trigger stops all roles | migration `0002` | `TestPostgresAuditGuarantees::test_the_app_role_cannot_rewrite_history` |
| Details encrypted per project; history verifiable after shredding | `application/audit.py` | `test_audit.py::TestAuditLog` |
| Sign-ins, failures, sign-outs and credential changes recorded | `application/accounts.py` | `test_auth_api.py::TestAuditTrail` |
| No email addresses or passwords in audit rows | `application/accounts.py` | `TestAuditTrail::test_audit_records_never_contain_email_addresses_or_passwords` |
| Logs redact secrets and personal data by field name | `observability.py` | *gap: no test yet* |

## Availability under load (ASVS V2, V13; `architecture.md` §5.7)

| Control | Where | Proven by |
| --- | --- | --- |
| No connection held across hashing or email | `application/accounts.py` | `TestNoConnectionIsHeldDuringSlowWork` |
| Bounded pool, fails fast; server-side statement and idle timeouts | `storage/postgres/storage.py` | `TestPostgresConcurrency` (pool, timeouts) |
| Saturation answers 503 with `Retry-After` | `api/errors.py` | `TestOverloadIsReportedNotCrashed` |
| Bounded memory; expired records purged | `ratelimit/memory.py`, `cli purge-expired` | `TestMemoryLimiterIsBounded`, `TestBoundedGrowth` |

## Gaps — known, not yet closed

| Gap | Plan |
| --- | --- |
| Multi-factor authentication (brief rule 6) | M9 |
| Projects, roles and membership checks (brief rules 7–8) | M10 |
| SSRF-guarded outbound HTTP (brief rule 10) | M11 |
| Log redaction has no test | next commit touching logging |
| SMTP over TLS untested (59% coverage of `email/smtp.py`) | M4, against a TLS mail server in CI |
| No real-browser test of cookies and CSRF | M13 (Playwright) |
| No load test across several API workers | M4 |
| No mutation testing, API fuzzing or SAST in CI | M4 (mutmut, Schemathesis, Semgrep/CodeQL) |
| No independent security review | Before any real data |
