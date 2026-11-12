# Security controls and the tests that prove them

Every control below is enforced in code **and** has a test that fails if the control is removed. A control without a test is listed under *Gaps*, not here.

References are to the requirements in [`WEB_APP_BRIEF.md`](../WEB_APP_BRIEF.md) ("brief rule n"), NIST SP 800-63B rev 4, and OWASP ASVS 5.0 **chapters** (V3 Web Frontend, V6 Authentication, V7 Session Management, V8 Authorization, V11 Cryptography, V13 Configuration, V16 Logging). This is a map of what is built, not a claim of ASVS compliance: that needs an independent assessment.

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
| Re-authentication and password change count against the same per-account limit as sign-in (10 per 15 min, 429), so a stolen session cannot guess the password | `application/accounts.py` (`_count_password_attempt`) | `test_auth_api.py::TestThrottling::test_repeated_wrong_passwords_at_reauthentication_are_throttled`, `::test_failed_sign_ins_count_against_reauthentication`, `::test_repeated_wrong_current_passwords_at_password_change_are_throttled` |
| Parallel guesses cannot exceed the limit | `application/accounts.py` (count before verify) | `test_concurrency_and_resources.py::TestThrottleUnderConcurrency` |
| The per-client limit counts the connecting address; `X-Forwarded-For` only from the operator's `trusted_proxies` (none by default; Caddy alone in compose) | `api/middleware.py` (`ClientAddressMiddleware`), `settings.py` | `test_auth_api.py::TestClientAddress`, `test_settings.py::TestTrustedProxies` |
| Registration by invitation only; first admin from the CLI | `routes/admin.py`, `cli.py` | `test_auth_api.py::TestInvitations`, `test_cli.py::TestCreateAdmin` |
| Reset links: single use (also under races), 30 min, newest wins | `application/accounts.py`, `storage/` (`claim`) | `test_auth_api.py::TestPasswordReset`, `test_storage_contract.py::TestPostgresConcurrency` |
| Reset requests reveal nothing about the account, not even by timing: the email is sent off the request path | `application/accounts.py` (`_send_later`) | `test_auth_api.py::TestPasswordReset::test_requesting_a_reset_looks_the_same_for_any_email`, `::test_a_request_never_waits_for_the_mail_server` |
| Tokens in emailed links travel in the URL fragment | `application/accounts.py` | `test_auth_api.py::TestPasswordReset::test_the_link_carries_the_token_in_the_fragment…` |
| Password change and reset notify the user by email | `application/accounts.py` | `TestChangePassword::test_the_user_is_told_by_email`, `TestPasswordReset::test_the_user_is_told…` |
| Passwords never accepted as CLI arguments | `cli.py` | `test_cli.py::TestCreateAdmin::test_the_password_is_never_accepted_as_an_argument` |

## Multi-factor authentication (brief rule 6; NIST 800-63B AAL2; ASVS V6)

| Control | Where | Proven by |
| --- | --- | --- |
| MFA required by default; an account without a factor reaches only enrolment, sign-out and its own basics | `api/session.py`, `domain/mfa.py` (`MfaPolicy`) | `test_mfa_api.py::TestMfaIsRequiredByDefault`, `test_mfa_api.py::TestUntilEnrolled` |
| Password then second factor: a pending session (aal 1, 5 min) reaches only `/auth/mfa/*` and sign-out | `application/accounts.py` (`login`), `api/session.py` | `test_mfa_api.py::TestTwoStepSignIn`, `test_mfa_domain.py::TestSessionAssurance` |
| Completing a factor rotates the token to a new aal 2 session (no fixation); a password change keeps the level | `application/mfa.py` (`_complete_sign_in`), `application/accounts.py` | `test_mfa_api.py::TestTwoStepSignIn::test_completing_mfa_issues_a_new_token_and_ends_the_pending_one`, `…::test_changing_the_password_keeps_the_session_at_aal2` |
| TOTP per RFC 6238 (SHA-1, 6 digits, 30 s, ±1 step) | `domain/mfa.py` | `test_mfa_domain.py::TestTotpCodes` (RFC vectors), `::TestTotpVerification` |
| TOTP secret: 160 bits, shown once, active only after a valid code, encrypted under the user's own data key | `application/mfa.py`, `application/encryption.py` | `test_mfa_api.py::TestTotpEnrolment` |
| A TOTP code is accepted once, also under concurrent requests | `domain/mfa.py` (`matching_totp_step`), `storage/` (`advance_step`) | `test_mfa_api.py::TestTotpIsSingleUse`, `test_mfa_storage_contract.py::TestPostgresSingleUseUnderRaces` |
| Second factors throttled per account like passwords (10 per 15 min), counted before checking | `application/mfa.py` | `test_mfa_api.py::TestSecondFactorThrottling` |
| Recovery codes: 10 × 80 bits, shown once, stored as HMACs keyed by a subkey of the user's data key | `domain/mfa.py`, `application/mfa.py`, `crypto/aesgcm.py` (HKDF) | `test_mfa_domain.py::TestRecoveryCodes`, `test_mfa_api.py::TestRecoveryCodes::test_codes_are_stored_only_as_keyed_hashes`, `…::test_codes_die_with_the_users_data_key` |
| Recovery codes single use (also under races); regenerating invalidates the old set; use is emailed | `storage/` (`claim`), `application/mfa.py` | `test_mfa_api.py::TestRecoveryCodes`, `TestPostgresSingleUseUnderRaces` |
| Passkeys: real WebAuthn ceremonies, user verification required, origin and RP id bound to `public_origin` | `auth/webauthn.py` | `test_mfa_api.py::TestPasskeyRegistration`, `test_mfa_api.py::TestPasskeySignIn` (software authenticator, forged fields) |
| WebAuthn challenges: single use (even after a failed answer), 5 min, bound to one session | `storage/` (`take`), `application/mfa.py` | `test_mfa_api.py::TestPasskeySignIn::test_a_challenge_is_spent_by_any_attempt_even_a_failed_one`, `TestPasskeyRegistration` |
| A passkey signature counter that does not advance is refused (cloned authenticator) | `domain/mfa.py`, `storage/` (`record_use`) | `test_mfa_api.py::TestPasskeySignIn::test_a_cloned_authenticator_is_caught_by_its_counter`, `test_mfa_domain.py::TestPasskeySignCount` |
| The last authenticator cannot be removed, also when two removals race | `application/mfa.py` (`_lock_enrolment`) | `test_mfa_api.py::TestRemovingAuthenticators` |
| Adding or removing a factor needs a recent sign-in, is emailed and audited without secrets | `application/mfa.py` | `test_mfa_api.py::TestTotpEnrolment`, `test_mfa_api.py::TestRemovingAuthenticators`, `test_mfa_api.py::TestMfaAuditTrail` |
| A password reset does not bypass MFA | `application/accounts.py` | `test_mfa_api.py::TestPasswordResetDoesNotBypassMfa` |

## Sessions (NIST AAL2; ASVS V7)

| Control | Where | Proven by |
| --- | --- | --- |
| 256-bit random tokens; only SHA-256 stored | `domain/sessions.py` | `test_sessions.py::TestTokens`, `test_auth_api.py::TestSessionCookie::test_a_session_token_is_never_stored_in_the_clear` |
| `__Host-` cookies: Secure, HttpOnly (session), SameSite | `api/session.py` | `test_auth_api.py::TestSessionCookie` |
| New token at every sign-in (no fixation) | `application/accounts.py` | `TestSessionCookie::test_signing_in_always_issues_a_new_token` |
| New token at step-up (re-authentication); the old one stops working; level and 24 h limit carry over | `application/accounts.py` (`reauthenticate`) | `test_auth_api.py::TestReauthentication`, `test_mfa_api.py::TestTotpEnrolment::test_the_rotated_session_keeps_its_second_factor_level` |
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
| Only JSON bodies accepted, except `multipart/form-data` on the document upload path alone | `api/middleware.py` (`multipart_paths`), `api/routes/documents.py` (`UPLOAD_PATH`) | `test_csrf.py::TestBodyFormat`, `test_document_uploads.py::TestCrossSiteRequests::test_multipart_is_refused_on_every_other_route` |
| The upload, the one non-JSON body, still refuses cross-site requests and needs the session's CSRF header, which a cross-site form cannot send | `api/middleware.py`, `api/session.py` | `test_document_uploads.py::TestCrossSiteRequests` |
| Security headers on every response (CSP, HSTS, nosniff…) | `api/middleware.py` | `test_http_hardening.py::TestSecurityHeaders` |
| Errors as problem documents; never echo input or internals | `api/errors.py` | `test_http_hardening.py::TestProblemResponses` |
| An unhandled error is a 500 problem with every security header and the request id; its log line has the same id | `api/middleware.py` (`UnhandledErrorMiddleware`) | `test_http_hardening.py::TestUnexpectedErrors` |
| An unreachable paper source or model server is a 502 naming no host, address or upstream text | `api/errors.py` | `test_http_hardening.py::TestUnavailableUpstreams` |
| Docs and `/info` absent in production | `main.py` | `test_http_hardening.py::TestProductionSurface` |
| Request bodies over 1 MiB refused before reading | `api/middleware.py` | `test_concurrency_and_resources.py::TestOverloadIsReportedNotCrashed` |
| Production refuses to start without its secrets | `settings.py` | `test_settings.py::TestProductionRequirements` |
| Production refuses every development convenience: the in-process worker, the demo model and paper source, the development administrator; none is on unless named | `settings.py` | `test_settings.py::TestDevelopmentConveniences` |
| A screening quote shown as the paper's words must be words of the masked text the model read: the stored quote is cut from that text, never the model's wording | `domain/screening.py` (`CriterionAnswer.located`), `application/screening.py` | `test_papers_api.py::TestCriterionAnswers`, `test_screening_domain.py::TestQuotes`, `::TestQuoteProperties` |
| A misspelt `HUMANI_T_` variable or secret file stops the process, naming the key but never its value | `settings.py` (`_UnknownNamesRefused`) | `test_settings.py::TestUnknownSettings` |
| A refused configuration never prints a secret or a URL password (raised, CLI, uvicorn startup) | `settings.py` (`hide_input_in_errors`) | `test_settings.py::TestSecretsNeverAppearInErrors` |

## Authorization (brief rules 7–8; ASVS V8)

| Control | Where | Proven by |
| --- | --- | --- |
| Admin-only endpoints refuse other users (403) and anonymous (401) | `application/accounts.py` | `TestInvitations::test_only_admins_can_invite` |
| Another user's session is invisible (404, not 403) | `application/accounts.py` | `TestSessionManagement::test_another_users_session_cannot_be_revoked_or_even_seen` |
| Disabled accounts cannot sign in; their sessions end | `application/accounts.py` | `TestLogin::test_a_disabled_account_cannot_sign_in` |
| No admin can disable their own account, nor the last enabled admin, also when admins race | `application/accounts.py`, `storage/` (`lock_enabled_admins`) | `test_auth_api.py::TestAccountAdministration`, `test_storage_contract.py::TestLastAdminUnderConcurrency`, `::TestPostgresAdminLock` |
| Re-enabling an account needs a recent sign-in, is audited, and revives no session | `application/accounts.py` (`enable_user`) | `test_auth_api.py::TestAccountAdministration` |
| Layering: no route or use case imports a concrete provider | `pyproject.toml` (import-linter) | `uv run lint-imports` |
| Role policy is one pure function, deny by default, tested as a role × action table | `domain/projects.py` (`can`) | `test_projects_domain.py::TestPolicy` |
| Every route needs sign-in except an explicit public allowlist | `api/session.py`, routes | `test_route_inventory.py::TestSignInIsRequired` |
| Non-members get `404` for everything about a project, with the same body as a missing project; instance admins included | `api/access.py`, `application/projects.py` | `test_route_inventory.py::TestMembershipIsRequired`, `test_projects_api.py::TestReadProject` |
| Members without the role get `403` | `api/access.py`, `application/projects.py` | `test_projects_api.py::test_owner_only_actions_are_refused_to_other_members_with_403` |
| Delete and member changes need a sign-in within 15 minutes; leaving does not | `application/projects.py` | `TestDeleteProject::test_deleting_needs_a_sign_in_within_15_minutes`, `TestMembers::test_member_changes_need_a_sign_in_within_15_minutes` |
| The last owner can never be demoted, removed or leave, also under concurrent requests | `domain/projects.py`, project row lock ([ADR-0002](../adr/0002-project-row-lock-for-membership-changes.md)) | `test_projects_domain.py::TestLastOwnerRule`, `test_projects_api.py::TestLastOwner`, `test_projects_storage_contract.py::TestLastOwnerUnderConcurrency`, `TestPostgresProjectLock` |
| No mass assignment: project and member bodies refuse unknown fields | `api/schemas/projects.py` | `TestCreateProject::test_fields_the_client_may_not_set_are_refused` |
| Run, event and gate routes find the project from the id they name; a non-member gets `404` with the same body as for a missing id, before any body is read | `routes/runs.py`, `application/runs.py`, `application/membership.py` | `test_runs_api.py::TestAccess`, `test_route_inventory.py::TestMembershipIsRequired` |
| Reviews: owners and reviewers ask; runs: owners and reviewers start and cancel; every member may follow a stream; viewers change nothing | `application/reviews.py`, `application/runs.py` | `test_runs_api.py::TestReviews`, `TestStartingARun`, `TestCancelling::test_a_viewer_may_not_cancel` |
| A privacy-review gate needs an owner (approving masking means reading raw text); a vocabulary gate an owner or reviewer | `domain/runs.py` (`action_to_resolve`) | `test_runs_domain.py::TestGates`, `test_runs_api.py::TestGates::test_only_owners_may_approve_a_privacy_review` |
| A gate is decided once, also under concurrent decisions; the loser gets `409` | `storage/` (`gates.resolve`, conditional) | `test_runs_storage_contract.py::TestPostgresUnderConcurrency::test_concurrent_resolutions_of_one_gate_have_exactly_one_winner`, `test_runs_api.py::TestGates::test_two_decisions_at_the_same_moment_have_one_winner` |
| Paper, PRISMA and manifest routes answer a non-member exactly as a missing review or run (404) | `application/papers.py` (`PaperService`) | `test_papers_api.py::TestPaperList::test_a_non_member_gets_the_same_404_as_for_a_missing_review`, `TestManifest::test_a_non_member_gets_404`, `test_route_inventory.py` |
| A live stream ends within a minute of its session being revoked or expiring, or its member leaving | `application/runs.py` (`_still_allowed`) | `test_runs_worker.py::TestTheLiveStream` |
| No mass assignment: review, run and gate bodies refuse unknown fields | `api/schemas/runs.py` | `test_runs_api.py::TestReviews::test_fields_the_client_may_not_set_are_refused` |

## Cryptography and data (brief rules 15, 17; ASVS V11)

| Control | Where | Proven by |
| --- | --- | --- |
| AES-256-GCM, random nonces, tamper detection | `crypto/aesgcm.py` | `test_encryption.py::TestAesGcmCipher` |
| Ciphertext bound to project, table, column and row | `domain/encryption.py` | `test_encryption.py::TestScopeEncryption` |
| Per-project data keys wrapped by an operator-held KEK | `crypto/local_key.py`, `application/encryption.py` | `test_encryption.py::TestLocalKeyProvider`, `TestScopeEncryption` |
| Deleting a project's key makes its data unreadable (crypto-shredding) | `application/encryption.py` | `TestScopeEncryption::test_shredded_scope_is_permanently_unreadable` |
| Shredding cannot be undone by a racing key rotation | `storage/` | `test_storage_contract.py::TestShreddingIsFinal` |
| KEK rotation keeps old data readable | `application/encryption.py` | `TestScopeEncryption::test_old_data_still_decrypts_after_kek_rotation` |
| Each project gets its own data key when it is created | `application/projects.py` | `test_projects_api.py::TestCreateProject::test_each_project_gets_its_own_live_data_key` |
| Project names are stored only encrypted under the project's key | `application/projects.py` | `TestCreateProject::test_the_name_is_never_stored_in_the_clear`, `TestRenameProject::test_the_new_name_is_not_stored_in_the_clear_either` |
| Deleting a project shreds its key; a copy taken before (a backup) can never be decrypted | `application/projects.py` | `TestDeleteProject::test_a_copy_taken_before_deletion_can_never_be_decrypted` |
| Review questions, run event payloads, gate payloads and gate revisions are stored only encrypted under the project's key | `application/reviews.py`, `application/run_log.py`, `application/run_worker.py` | `test_runs_api.py::TestReviews::test_the_question_is_never_stored_in_the_clear`, `TestPrivacy::test_event_payloads_are_stored_only_as_ciphertext`, `TestGates::test_revisions_reach_the_stage_and_are_stored_encrypted` |
| Paper records, kept texts, compiled queries and screening rationales are stored only encrypted under the project's key, each bound to its row; deleting the project makes them unreadable | `application/papers.py` (`PaperStore`) | `test_pipeline_stages.py::TestDiscover::test_paper_records_are_stored_only_sealed`, `TestIngest::test_kept_text_is_sealed`, `TestScreen::test_rationales_are_stored_only_sealed`, `TestDeletedProjects` |
| A paper's identity (DOI, or title and year) is stored only as an HMAC under a subkey of the project's key | `domain/discovery.py` (`identity_digest`) | `test_screening_domain.py::TestDiscovery::test_an_identity_digest_depends_on_the_key`, `test_pipeline_stages.py::TestDiscover::test_paper_records_are_stored_only_sealed` |
| Members see paper text masked only; no route serves raw kept text | `api/schemas/papers.py`, `application/papers.py` | `test_papers_api.py::TestPaperList::test_the_list_never_carries_raw_text`, `TestPaperDetail::test_a_paper_shows_its_decision_with_full_provenance_and_masked_text` |
| An event payload is bound to its run and id, so the log cannot be reordered | `application/run_log.py` | `test_runs_api.py::TestPrivacy::test_an_event_payload_cannot_be_moved_to_another_event` |
| Deleting a project makes its run log unreadable and its streams `404`; its active runs end without blocking the queue | `application/run_log.py`, `application/run_worker.py` | `TestPrivacy::test_deleting_the_project_makes_every_event_unreadable`, `test_runs_worker.py::TestDeletedProjects` |
| Events and audit rows never carry the question; a failed run shows plain language, never exception text | `domain/runs.py` (`stage_failed_message`), `application/run_worker.py` | `TestPrivacy::test_no_raw_question_text_appears_in_events_or_audit_rows`, `TestFailure::test_an_unexpected_error_fails_the_run_in_plain_language` |
| Event data is one line of ASCII JSON, so no value can end an event early or inject an SSE field | `api/sse.py` | `test_runs_api.py::TestTheEventStream::test_each_event_has_an_id_a_name_and_one_line_of_json` |
| Plan versions, clarifying answers, change requests and privacy receipts are stored only encrypted under the project's key, each bound to its row; deleting the project makes them unreadable | `application/planning.py` | `test_plans_api.py::TestWhatIsStored` |
| An accepted plan version is never rewritten; the first accept wins, also when two people accept at once | `domain/plans.py`, `storage/postgres/plans.py`, migration 0008 (trigger) | `test_plans_domain.py::TestReviewPlan`, `test_plans_storage_contract.py::TestAccept`, `::TestPostgresUnderConcurrency`, `::TestPostgresSchema`, `test_plans_api.py::TestAccept` |
| A decision brief's question, body, evidence and trace are stored only encrypted under the project's key, each bound to the brief's row; deleting the project makes them unreadable | `application/briefs.py` | `test_briefs_stage.py::TestStoredSafely` |
| A run stores at most one brief, also when its stage runs again after a crash or two workers race | `storage/` (`briefs.add`, unique `run_id`), `application/briefs.py` | `test_briefs_storage_contract.py::TestBriefStorage`, `test_briefs_stage.py::TestRunningAgain` |
| Follow-up questions and answers (with their evidence and agent steps) are stored only encrypted under the project's key, each bound to its row; deleting the project makes them unreadable | `application/questions.py` | `test_questions_api.py::TestStoredSafely` |
| A question is answered by one worker under a lease; a lapsed lease is retried once, then failed in plain words; a thread takes one open question at a time | `domain/questions.py`, `storage/` (`claim_next`, `renew_lease`) | `test_questions_storage_contract.py`, `test_questions_api.py::TestTheQueue`, `TestThreads::test_a_thread_answers_one_question_at_a_time` |
| Exports hold exactly what the screens show (the same response models), are generated on request and never stored, carry `Cache-Control: no-store`, `nosniff` and an attachment name built from ids only | `application/exports.py`, `api/exports.py`, `domain/exports.py` | `test_exports_api.py`, `test_exports_domain.py::TestFilenames` |
| CSV formula injection: a cell starting with `=`, `+`, `-`, `@`, a tab or a carriage return is neutralised | `domain/exports.py` (`neutralise_cell`) | `test_exports_domain.py::TestCsvInjection`, `test_exports_api.py::TestDataset::test_a_cell_a_spreadsheet_would_run_is_neutralised` |
| The print brief escapes all text and runs no script | `api/exports.py` | `test_exports_api.py::TestBrief::test_the_print_brief_escapes_the_models_text` |

## Audit (brief rule 16; ASVS V16)

| Control | Where | Proven by |
| --- | --- | --- |
| Hash-chained events; edits, deletions, reordering detected | `domain/audit.py` | `test_audit.py::TestVerifyChain` |
| Concurrent writers cannot fork the chain | `storage/postgres/` (head lock) | `test_storage_contract.py::TestPostgresAuditGuarantees` |
| App role cannot `UPDATE`/`DELETE` events; trigger stops all roles | migration `0002` | `TestPostgresAuditGuarantees::test_the_app_role_cannot_rewrite_history` |
| Every export and every follow-up question is audited with ids only | `application/exports.py`, `application/questions.py` | `test_exports_api.py::TestEveryDownload::test_each_download_is_audited_with_ids_only`, `test_questions_api.py::TestAsking::test_asking_is_audited_with_ids_only` |
| Details encrypted per project; history verifiable after shredding | `application/audit.py` | `test_audit.py::TestAuditLog` |
| Sign-ins, failures, sign-outs and credential changes recorded | `application/accounts.py` | `test_auth_api.py::TestAuditTrail` |
| No email addresses or passwords in audit rows | `application/accounts.py` | `TestAuditTrail::test_audit_records_never_contain_email_addresses_or_passwords` |
| Every project and membership change recorded, ids only, scoped to the project | `application/projects.py` | `test_projects_api.py::TestAuditTrail` |
| The chain still verifies after a project is deleted | `application/projects.py` | `TestDeleteProject::test_the_audit_chain_still_verifies_after_deletion` |
| Creating a review, starting and cancelling a run, and deciding a gate are recorded, ids only, scoped to the project | `application/reviews.py`, `application/runs.py` | `test_runs_api.py::TestReviews::test_creating_a_review_is_audited_with_ids_only`, `TestStartingARun::test_starting_a_run_is_audited_with_ids_only`, `TestCancelling::test_cancelling_is_audited_with_ids_only`, `TestGates::test_resolving_a_gate_is_audited_with_ids_only` |
| Every screening decision is audited (`screening.decided`), ids only, and stored with its model id, prompt version and criteria version; the app role cannot rewrite a decision | `application/screening.py`, migration `0009` (`REVOKE UPDATE`) | `test_pipeline_stages.py::TestScreen::test_every_decision_is_audited_with_ids_only`, `::test_every_paper_with_an_abstract_gets_one_decision_with_its_provenance` |
| The app role cannot rewrite a stored run event | migration `0007` | `test_runs_storage_contract.py::TestPostgresUnderConcurrency::test_the_app_role_cannot_rewrite_a_stored_event` |
| Logs redact secrets and personal data by field name | `observability.py` | `test_observability.py::TestRedaction` |

## Availability under load (ASVS V2, V13; `architecture.md` §5.7)

| Control | Where | Proven by |
| --- | --- | --- |
| No connection held across hashing or email | `application/accounts.py` | `TestNoConnectionIsHeldDuringSlowWork` |
| Bounded pool, fails fast; server-side statement and idle timeouts | `storage/postgres/storage.py` | `TestPostgresConcurrency` (pool, timeouts) |
| Saturation answers 503 with `Retry-After` | `api/errors.py` | `TestOverloadIsReportedNotCrashed` |
| Rate limiter outage fails closed: 503 with `Retry-After`, no un-throttled attempt, no spent code | `ratelimit/valkey.py`, `api/errors.py` | `test_mfa_api.py::TestRateLimiterOutage` |
| Bounded memory; expired records purged | `ratelimit/memory.py`, `cli purge-expired` | `TestMemoryLimiterIsBounded`, `TestBoundedGrowth` |
| A stream never holds a database connection while it waits | `application/runs.py` | `test_runs_worker.py::TestTheLiveStream::test_a_stream_never_holds_a_database_connection_while_it_waits` |
| A run waiting at a gate holds no worker and no lease | `application/run_worker.py` | `test_runs_worker.py::TestGatesAtTheWorker::test_waiting_holds_no_lease_and_no_worker`, `test_runs_api.py::TestGates::test_a_gate_pauses_the_run_and_frees_the_worker` |
| Screening sends at most `HUMANI_T_SCREENING_CONCURRENCY` papers (default 2) to the model at once | `application/screening.py` | `test_pipeline_stages.py::TestScreen::test_screening_runs_at_most_the_configured_number_of_papers_at_once` |
| A crashed worker's run is resumed or failed once its lease expires, never stuck; a worker that lost its lease can write nothing more | `domain/runs.py` (`recovery_for`), `application/run_worker.py`, `storage/` (`next_id` lease check) | `test_runs_worker.py::TestCrashRecovery`, `test_runs_storage_contract.py::TestEventLog::test_a_worker_may_add_events_only_while_it_holds_the_lease` |
| Exactly one `job.finished`, always last; nothing can follow it | `application/run_log.py`, `storage/` (`next_id`) | `test_runs_worker.py::TestExactlyOneFinish`, `test_runs_storage_contract.py::TestEventLog::test_no_event_can_follow_a_finished_run` |
| Two workers never take one run and never wait for each other (`SKIP LOCKED`); event ids never repeat under concurrent writers | `storage/postgres/runs.py` | `test_runs_storage_contract.py::TestPostgresUnderConcurrency` |
| A Valkey outage slows wake-ups to polling; nothing fails | `signals/valkey.py` | `test_signals_contract.py::TestValkeyOutage` |

## Outbound requests (brief rules 1, 10; `architecture.md` §5.6; ASVS V13)

| Control | Where | Proven by |
| --- | --- | --- |
| Each fetcher reaches only its allowlisted hosts; exact match, no subdomains | `net/base.py`, `net/httpx_fetcher.py` | `test_net_fetcher.py::TestAllowlist` |
| HTTPS only, except an operator-named internal destination | `net/httpx_fetcher.py` | `TestAllowlist::test_plain_http_is_refused_for_a_public_destination` |
| Private, loopback, link-local, CGNAT, multicast, reserved and metadata addresses refused | `net/addresses.py` | `test_net_addresses.py::TestBlockedAddresses`, `test_net_fetcher.py::TestBlockedAddresses` |
| IPv4-mapped, NAT64, 6to4 and Teredo forms judged by (or refused for) what they embed | `net/addresses.py` | `test_net_addresses.py::TestBlockedAddresses` |
| Decimal, octal, hex and short IPv4 spellings read as the address they mean | `net/addresses.py` | `test_net_addresses.py::TestHostLiterals`, `TestBlockedAddresses::test_an_address_literal_is_checked…` |
| Address checked at connect time and the socket opened to that address (no DNS rebinding) | `net/httpx_fetcher.py` (`_GuardedNetwork`) | `test_net_fetcher.py::TestDnsRebinding` |
| Host header and TLS server name keep the original host | `net/httpx_fetcher.py` | `TestDnsRebinding::test_the_host_header_and_tls_server_name_keep_the_hostname` |
| Redirects followed by hand, each hop re-checked, at most 5 | `net/httpx_fetcher.py` | `test_net_fetcher.py::TestRedirects` |
| Credentials bound to one destination; never forwarded to another host | `net/base.py` (`Destination`) | `TestRedirects::test_a_bearer_token_is_sent_only_to_the_destination_it_belongs_to`, `TestRequests::test_callers_cannot_attach_credentials_or_a_host` |
| Response size capped (declared and streamed); connect, read and total timeouts | `net/httpx_fetcher.py` | `test_net_fetcher.py::TestLimits` |
| No HTTP client outside `app.net` | `pyproject.toml` (import-linter) | `uv run lint-imports` ("Only app.net opens outbound HTTP connections") |
| No paper source is contacted unless the operator lists it | `settings.py`, `api/dependencies.py` | `test_outbound_wiring.py::TestPaperSources` |
| Each source's published rate limit kept per process (arXiv 1 per 3 s, one at a time) | `net/pacing.py`, `search/*.py` | `test_net_pacing.py`, `test_search_sources.py::TestArxiv::test_requests_are_spaced_at_least_three_seconds_apart` |
| User terms reach a source exactly as given; a quote cannot inject operators | `search/_remote.py` (`phrase`) | `test_search_sources.py` (`test_a_strict_vocabulary_reaches_…`, `test_a_quote_inside_a_term_cannot_break_out_of_its_phrase`) |
| XML from arXiv with a DTD refused before parsing | `search/arxiv.py` | `TestArxiv::test_a_document_type_declaration_is_refused_before_parsing` |

## Model calls (brief rules 1, 9, 11; CLAUDE.md "the one rule")

| Control | Where | Proven by |
| --- | --- | --- |
| The default model server is one the operator runs (`http://ollama:11434`) | `settings.py` | `test_settings.py::TestInferenceDestination::test_the_default_is_the_compose_model_server` |
| Known hosted model APIs, and Ollama `*-cloud` models, refused unless `allow_hosted_inference` | `settings.py` | `TestInferenceDestination::test_a_hosted_inference_api_is_refused_in_any_environment`, `…_ollama_cloud_model_…` |
| Production refuses plain http to a model server outside the deployment | `settings.py` | `TestInferenceDestination::test_production_refuses_plain_http_to_a_host_outside_the_deployment` |
| The model server's token reaches only the model server | `api/dependencies.py` | `test_outbound_wiring.py::TestInference` |
| Every model call passes the prompt guard first; a refusal or a guard error stops it | `application/inference.py` | `test_inference.py::TestPromptGuard` |
| Model answers validated against the requested JSON schema; nothing unchecked returned | `inference/schema.py`, `inference/ollama.py`, `inference/openai_compatible.py` | `test_inference.py::TestOllama`, `TestOutputValidation`, `test_inference_openai.py::TestStrictMode::test_a_dropped_keyword_is_still_enforced_on_the_answer` |
| A hosted model API is reached only with `inference_api=openai` and `allow_hosted_inference` (a `compose.yaml` edit); its key travels only to that host | `settings.py`, `api/dependencies.py` | `test_settings.py::TestInferenceDestination::test_choosing_the_openai_format_does_not_open_hosted_apis`, `test_outbound_wiring.py::TestInference::test_a_hosted_api_key_travels_only_to_that_api` |
| A model provider's own error text is never repeated in our errors or logs (it can echo the request) | `inference/openai_compatible.py` | `test_inference_openai.py::TestFailures` |
| Country and large-region names are kept in questions only when the operator turns it on; smaller places stay masked and project rules still win | `privacy/sanitizer.py`, `domain/geography.py` | `test_privacy_sanitizer.py::TestCountriesAndRegions`, `test_geography_domain.py` |
| A refused screening answer is logged with ids and a reason, never the paper's text or the model's words | `application/screening.py` | `test_pipeline_stages.py::TestScreen::test_a_refused_answer_is_logged_with_its_reason_and_never_the_text` |
| The question, clarifying answers and change requests are masked before any planning call | `application/planning.py` | `test_plans_api.py::TestStartingAPlan::test_the_question_is_masked_before_the_model_sees_it`, `TestAnswers::test_answers_are_masked_…`, `TestChanges::test_the_change_request_is_masked_…` |
| A planning call the guard refuses sends nothing, stores nothing and leaves no receipt | `application/planning.py` | `test_plans_api.py::TestStartingAPlan::test_a_call_the_prompt_guard_refuses_…` |
| Every planning call leaves a receipt of exactly the text sent, the current plan's JSON included, even when the call fails | `application/planning.py` | `test_plans_api.py::TestPrivacyReceipts`, `TestStartingAPlan::test_an_unreachable_model_…` |
| Planning and screening calls go through `GuardedInference.with_rules` with the project's rules, so model-written text (clarifying questions, the current plan) and paper text holding an always-masked term are refused, not sent | `application/planning.py`, `application/screening.py` | `test_plans_api.py::TestProjectMaskingRules::test_model_text_holding_a_masked_term_is_refused_and_never_sent`, `test_pipeline_stages.py::TestScreen::test_paper_text_holding_a_masked_term_is_stopped_by_the_privacy_check` |
| Plan text a person writes (uploaded terms and criteria, vocabulary-gate edits) is refused with a 422 naming the field when it holds a term the project always masks; a plan that gains one through a later rule cannot be revised or screened until it is changed | `domain/plans.py` (`refuse_rule_terms`), `application/planning.py`, `application/runs.py`, `application/screening.py` | `test_plans_domain.py::TestMaskedTermsInPlanText`, `test_plans_api.py::TestProjectMaskingRules`, `test_queries_stage.py::TestMaskedTermsAtTheGate`, `test_pipeline_stages.py::TestScreen::test_a_criterion_holding_a_masked_term_fails_the_stage_and_is_never_sent` |
| A user-supplied vocabulary never reaches the model or the sanitiser, and compiles to exactly the supplied terms | `domain/plans.py`, `application/planning.py`, migration 0008 | `test_plans_domain.py::TestReviewPlan::test_a_strict_vocabulary_can_never_be_written_by_the_model`, `TestStrictTextFormat::test_a_strict_vocabulary_compiles_to_exactly_the_supplied_terms`, `test_plans_api.py::TestStrictUpload`, `test_queries_stage.py::…::test_a_strict_vocabulary_goes_through_the_gate_without_the_model`, `test_plans_storage_contract.py::TestPostgresSchema` |
| Model-drafted terms holding a masked placeholder, in any bracket or case, are dropped, so masked text never becomes a search term | `application/planning.py`, `domain/masking.py` | `test_plans_api.py::TestChanges::test_model_terms_that_hold_masked_text_are_dropped_and_noted`, `::test_model_terms_that_hold_a_rewritten_placeholder_are_dropped_too`, `test_masking_domain.py::TestHoldsPlaceholder` |
| The screen stage sends only masked text, through `GuardedInference`; a paper not yet masked is never sent, and a refused prompt is counted failed, not retried elsewhere | `application/screening.py` | `test_pipeline_stages.py::TestScreen::test_the_model_only_ever_sees_masked_text`, `::test_a_paper_not_yet_masked_is_never_sent_and_is_counted_as_skipped`, `::test_a_paper_the_privacy_check_refuses_is_counted_failed_and_not_decided` |
| A paper source's failure is recorded and shown by source id; its internal reason goes to the operator's log only | `application/pipeline.py` | `test_pipeline_stages.py::TestDiscover::test_a_source_failure_never_shows_its_internal_reason` |
| The brief stage sends only the masked question and masked passages, through `GuardedInference.with_rules` with the project's rules | `application/briefs.py`, `application/brief_loop.py` | `test_briefs_stage.py::TestMaskedTextOnly` |
| A brief's citations are checked by code against the passages actually retrieved; an invented tag is reported and sent back for revision, and a draft with failing checks is never accepted | `domain/briefs.py` (`check_citations`), `application/brief_loop.py` | `test_briefs_domain.py::TestCitationCheck`, `test_briefs_loop.py::TestChecksCatchInventedCitations` |
| A brief never names a winner, a rank or a total score | `domain/briefs.py` | `test_briefs_domain.py::TestNoWinner`, `test_briefs_api.py::TestReadingTheBrief::test_nothing_in_the_brief_names_a_winner_a_rank_or_a_total` |
| A refused prompt or an unreachable model server fails the brief in plain words, with no host or prompt text in the step or the run's error | `application/brief_loop.py` | `test_briefs_loop.py::TestRefusalsAndOutages` |
| A follow-up question is masked with the question sanitiser and the project's rules before it is stored or sent; the raw question is never stored, and only masked text reaches the model | `application/questions.py`, `application/answer_loop.py` | `test_questions_api.py::TestAsking::test_the_raw_question_is_never_stored`, `TestAnswering::test_only_masked_text_reaches_the_model`, `TestThreads::test_a_follow_up_carries_the_thread_masked` |
| An answer's citations are checked by code against what was retrieved; nothing retrieved is answered "Nothing in this project answers that" without a model call | `domain/questions.py` (`check_answer`), `application/answer_loop.py` | `test_questions_api.py::TestAnswering` |

## Uploaded documents (brief rules 2, 3, 12, 13, 17; OWASP File Upload; ASVS V5)

The walkthrough is [16 — Documents](../walkthroughs/16-documents.md); the decisions are [ADR-0008](../adr/0008-uploaded-files-on-a-sealed-volume.md) and [ADR-0009](../adr/0009-local-extraction-in-an-isolated-process.md).

| Control | Where | Proven by |
| --- | --- | --- |
| Only owners upload, delete, see raw text, correct masks and approve; any member reads masked text; non-members get 404 before the body is read | `api/routes/documents.py`, `application/documents.py` | `test_documents_api.py::TestReview::test_only_an_owner_sees_the_raw_text`, `test_document_uploads.py::TestRefusals::test_a_reviewer_cannot_upload`, `…::test_a_non_member_gets_404_and_nothing_is_read`, `test_route_inventory.py` |
| The type is recognised from the file's bytes (PDF, DOCX, UTF-8 text, Markdown), never from its name or the browser's `Content-Type`; a macro-enabled document is refused | `domain/documents.py` (`detect_document_type`) | `test_documents_domain.py::TestTypeDetection`, `test_document_uploads.py::TestRefusals::test_the_type_comes_from_the_bytes_not_the_name_or_the_browser` |
| Size capped (`upload_max_bytes`, 20 MiB), checked on the declared length before reading and while reading; the parser keeps the file in memory and never spools it to disk unencrypted | `api/middleware.py` (`larger_limits`), `api/uploads.py` | `test_document_uploads.py::TestRefusals::test_a_file_over_the_size_limit_is_refused`, `…::test_a_declared_body_over_the_limit_is_refused_before_it_is_read`, `TestMultipartParser` |
| Exactly one file part named `file`; anything else refused | `api/uploads.py` | `test_document_uploads.py::TestMultipartParser` |
| File names shown without paths, hidden or bidirectional characters, or unsafe characters, at most 200 characters; stored only sealed; files are stored by id, never by name | `domain/documents.py` (`safe_filename`), `application/documents.py` | `test_documents_domain.py::TestSafeFilename`, `test_documents_api.py::TestSealing::test_names_and_texts_are_stored_only_sealed` |
| Per-project quotas (count and bytes) hold under concurrent uploads (project row lock); uploads per person per hour are counted before the body is read; a refused upload stores nothing | `domain/documents.py` (`UploadPolicy`), `application/documents.py` (`admit_upload`, `upload`) | `test_document_uploads.py::TestRefusals` |
| **Malware scanning is the operator's explicit choice.** `malware_scanner=clamd` scans every upload with the operator's ClamAV daemon; `malware_scanner=none` skips scanning and records `scanned_with: none` on every document. Left unset in production, uploads are refused with a `503` that says why | `settings.py` (`uploads_disabled_reason`), `api/dependencies.py`, `malware/` | `test_documents_settings.py::TestScannerChoice`, `test_document_uploads.py::TestRefusals::test_uploads_switched_off_by_the_operator_are_refused_with_the_reason` |
| An infected file is deleted unread and the document fails with a fixed message; the signature is audited, sealed | `application/documents.py` (`DocumentProcessor._scan`) | `test_documents_api.py::TestProcessing::test_an_infected_file_is_deleted_unread_and_fails_plainly` |
| The ClamAV client speaks clamd's `INSTREAM` protocol with the standard library; an unreachable scanner leaves the document queued, unread | `malware/clamd.py` | `test_document_seams.py::TestClamd`, `test_documents_api.py::TestProcessing::test_an_unavailable_scanner_leaves_the_document_queued_unread` |
| In the compose stack, clamd runs as a non-root user with a read-only filesystem and no capabilities, on an internal network only the worker joins: the API cannot reach it and it cannot reach the internet. Its updater, freshclam, runs apart, on a network of its own, and never sees a file ([ADR-0012](../adr/0012-clamav-in-the-stack.md)) | `compose.yaml` (`clamav`, `freshclam`, networks `scan` and `signatures`), `infrastructure/clamav/` | `.github/workflows/compose.yml` › "Every container is hardened", "Only the worker reaches clamd, and clamd reaches nothing" |
| Files are sealed with the project's data key, bound to the document's id, before they reach the volume; deleting the project makes them unreadable | `application/documents.py` (`DocumentSeal`), `files/volume.py` | `test_documents_api.py::TestSealing`, `TestDeletion::test_deleting_the_project_makes_the_file_and_text_unreadable` |
| File keys are two UUIDs; nothing else reaches the volume's paths; files are never overwritten | `files/base.py`, `files/volume.py` | `test_document_seams.py::TestFileStores` |
| Extraction runs in the worker, never in the request, in a child process with no environment, which refuses network, processes and file writes, and is killed at its timeout | `extraction/isolated.py`, `extraction/_child.py` | `test_document_extraction.py::TestIsolatedExtractor`, `TestChildRefusesSideEffects` |
| DOCX: no document type declarations (no XXE, no entity bombs); members, declared sizes and compression ratios bounded before inflating, real bytes counted while inflating | `extraction/local.py` | `test_document_extraction.py::TestDocx` |
| PDF: pages and characters bounded; password-protected and damaged files fail with a fixed reason, never the parser's message | `extraction/local.py` | `test_document_extraction.py::TestPdf` |
| Documents are always masked with the broad list, dates included, plus the project's rules; this cannot be switched off | `api/dependencies.py` (`_document_sanitizer`), `privacy/dates.py` | `test_documents_api.py::TestProcessing::test_the_worker_scans_reads_and_masks_with_the_broad_list`, `…::test_project_rules_reach_documents_too`, `test_documents_settings.py::TestWiring` |
| Raw text is shown only to owners, only in the review, and each opening is audited | `application/documents.py` (`review`) | `test_documents_api.py::TestReview::test_every_opening_of_raw_text_is_audited_with_ids_only` |
| "Not personal" is never allowed for detected structured identifiers or project rules, needs a reason, and the reason is stored sealed | `domain/documents.py` (`removable_spans`) | `test_documents_domain.py::TestCorrections`, `test_documents_api.py::TestReview::test_a_structured_identifier_can_never_be_unmasked` |
| No model ever sees a document that is not approved; an approval names the masked version, and a later correction withdraws it until approved again | `domain/documents.py` (`Document.decided`), `application/documents.py` (`DocumentEvidence`) | `test_documents_api.py::TestEvidence::test_nothing_unapproved_ever_becomes_evidence`, `TestApproval::test_a_correction_after_approval_withdraws_it_from_the_evidence`, `…::test_approving_a_version_no_longer_current_is_a_conflict` |
| Who approved which masked version, and when, is kept append-only (the app role has no `UPDATE`) and outlives the document | `document_reviews`, migration 0013 | `test_documents_api.py::TestStoredReviewsArePermanent`, `test_documents_storage_contract.py::TestTextsAndMasks::test_a_review_outlives_its_document` |
| No database connection is held across file I/O, scanning or extraction | `application/documents.py` | `test_documents_api.py::TestProcessing::test_no_database_connection_is_held_across_file_io_scanning_or_extraction` |
| Every document action is audited with ids only: never a name, text or reason | `application/documents.py` | `test_documents_api.py::TestApproval::test_decisions_are_audited_with_ids_only`, `TestReview::test_every_opening_of_raw_text_is_audited_with_ids_only` |

## Deployment and supply chain (brief rules 15, 17; ASVS V13, V15; `architecture.md` §2)

Proven by CI jobs rather than pytest: each job fails if the control is removed. Workflows are in `.github/workflows/`; the walkthrough is [14 — Deploy and CI](../walkthroughs/14-deploy-and-ci.md).

| Control | Where | Proven by |
| --- | --- | --- |
| HTTPS only; plain HTTP redirects | `infrastructure/caddy/Caddyfile` (automatic HTTPS) | `compose.yml` › *Plain HTTP redirects to HTTPS* |
| SPA served with a strict CSP: no `unsafe-inline`, Trusted Types required, `frame-ancestors 'none'` | `Caddyfile` | `compose.yml` › *Security headers* (`.github/scripts/check-security-headers.sh`) |
| Styles allowed only with a per-response nonce (Chakra UI writes them at run time); the page carries the header's nonce, a new one per response, and hashed assets are never templated | `Caddyfile` (`templates @page`, `{http.request.uuid}`), `apps/web/csp.config.ts` | `apps/web/src/csp.test.ts`; `compose.yml` › *Security headers* (*the page's style nonce*) |
| HSTS, nosniff, `no-referrer`, COOP and CORP on every response; no `Server` or `Via` header | `Caddyfile`, `api/middleware.py` | `compose.yml` › *Security headers* |
| Request bodies over 1 MiB refused at the proxy, before the API | `Caddyfile` | `compose.yml` › *Oversized request bodies are refused at the edge* |
| Every container runs as a non-root user | `services/api/Dockerfile`, `apps/web/Dockerfile`, `compose.yaml` | `compose.yml` › *Every container runs as a non-root user* |
| Read-only root filesystem, all capabilities dropped, `no-new-privileges`, in every container | `compose.yaml` | `compose.yml` › *Every container is hardened and publishes only what it should* |
| Only Caddy publishes ports; Postgres and Valkey are on an internal network | `compose.yaml` | the same step |
| Secrets reach containers as files, each only its own; none in environment variables | `compose.yaml` | `compose.yml` › *Each container holds only its own secrets* |
| Migrations run as the schema owner in a one-shot job; the API never holds that credential | `compose.yaml` (`migrate`) | the same step |
| Every base and service image pinned by digest | Dockerfiles, `compose*.yaml` | `compose.yml` › *Every image is pinned by digest* |
| Development runs the same images as production | `compose.dev.yaml` | `compose.yml` › *Development images match the self-hosted stack* |
| Every GitHub Action pinned to a commit SHA; least-privilege tokens; no persisted credentials | `.github/workflows/` | `security.yml` › zizmor |
| No secret in the git history | | `security.yml` › gitleaks |
| No dependency with a known vulnerability | `uv.lock`, `pnpm-lock.yaml` | `security.yml` › osv-scanner |
| No fixable HIGH or CRITICAL vulnerability in the images | images | `security.yml` › trivy |
| Static analysis of Python, TypeScript and the workflows (SAST) | | `codeql.yml` |

## Gaps — known, not yet closed

| Gap | Plan |
| --- | --- |
| A project whose every owner is disabled cannot be managed or deleted; there is no admin recovery | Later, with an audited operator command |
| Refused project actions (403, 404) are not audited | Revisit with the threat model (M14) |
| The prompt guard allows everything: no leak check yet before a model call (brief rule 1) | Masking lane, `architecture.md` §3.9.1 |
| Rate limits are per process; N workers can send N times a source's limit | A shared limiter in Valkey, when more than one process searches |
| Outbound refusals are logged but not audited | Revisit with the threat model (M14) |
| SMTP over TLS untested (59% coverage of `email/smtp.py`) | A CI job against a TLS mail server; not part of the M4 containers work |
| No real-browser test of cookies and CSRF | M13 (Playwright) |
| Passkeys are tested with a software authenticator only, not a browser's | M13 (Playwright with a virtual WebAuthn authenticator) |
| Passkey attestation is not checked (`attestation: "none"`): any authenticator model is accepted | Revisit if an operator must restrict authenticator models |
| An account that loses every factor and every recovery code has no self-service way back | Later, with an audited operator command |
| No load test across several API workers (the stack runs two by default) | Open: a load test against the compose stack |
| No mutation testing or API fuzzing in CI (SAST is closed by CodeQL) | Open: mutmut, Schemathesis against the compose stack |
| SSE through Caddy (`flush_interval -1`) is configured but not tested end to end | The compose smoke test, once `GET /api/v1/runs/{id}/events` exists |
| Trusted Types and the CSP are checked as headers, not in a browser running the app | M13 (Playwright against the compose stack, failing on any CSP violation) |
| **Uploads with `malware_scanner=none`** are not scanned. It is visible on every document (`scanned_with: none`) and must be an operator's written decision; production refuses uploads while the setting is unset. The compose stack's ClamAV is opt-in (profile `malware-scan`), so a new deployment scans nothing until the operator turns it on | Owner's decision, open in [ADR-0012](../adr/0012-clamav-in-the-stack.md): make `malware-scan` and `clamd` the compose default |
| Caddy refuses request bodies over 1 MiB on `/api/*`, so uploads larger than that fail at the edge in the compose stack | Give `POST /api/v1/projects/*/documents` its own `request_body` limit (`upload_max_bytes` + 64 KiB) in the Caddyfile |
| The extraction child's audit hook is defence in depth, not a sandbox; native code could bypass it | Run extraction in its own container with no network when native parsers (mmore) arrive (ADR-0009) |
| Text in images (scans, photographs) is not read, so it is neither masked nor reviewed | OCR behind the same extractor seam; the review screen says so |
| Plan text a person writes (criteria, uploaded terms, gate edits) is checked against the project's rules and the pattern guard only: a name or place typed there that matches no rule reaches the model and the paper sources unmasked | Owners add a rule for anything sensitive; name detection on plan text is a later decision, since it would mask places and populations a criterion needs |
| Rules added after a document's approval reach it only at its next correction | Re-mask approved documents monotonically (adding masks only) when a rule is added |
| Behind Docker Desktop every client shares one address, so per-IP throttling is per-stack there | Production is Linux, where Docker preserves client addresses; documented in `docs/deploy/self-hosting.md` |
| Valkey uses one shared password, not per-client ACLs | With the job queue's final command set |
| No independent security review | Before any real data |
| A stage that never calls `raise_if_cancelled` is cancelled only when it finishes; cancellation is cooperative | With the real stages, which check between items |
| Watching a stream is not activity: a member who only watches a long run is signed out after an hour idle, and the stream ends | Revisit with the web app if it proves annoying (UI_UX_SPEC §11.4) |
| The worker runs fake stages for mask, index and answer until those lanes land; Screen reads masked text from a placeholder that has none, so nothing is screened until the mask store is wired | Mask and brief lanes |
| Screening reads abstracts only; open-access full text with PDF extraction is not built | P2 |
