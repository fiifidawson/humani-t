# 06 — Multi-factor authentication

> **Milestones:** M9, plus gap G1 · **Status:** done · **Previous:** [05 — Accounts and sessions](05-accounts-and-sessions.md) · **Next:** [07 — Projects and roles](07-projects-and-roles.md)

A password is no longer enough. Every account must add a second factor — a passkey, or an authenticator app on a phone — before it can do anything beyond its own account basics, and from then on signing in takes two steps: the password yields a short **pending** session that can only complete the second step, and the second factor replaces it with a full session under a new token. Recovery codes cover a lost phone. Second factors are single-use and throttled like passwords; their secrets are encrypted or keyed under the user's own data key; adding or removing one needs a recent sign-in, emails the user, and is audited. This phase serves brief rule 6 and [`architecture.md` §5.1–5.2](../architecture.md#51-sign-in) at NIST SP 800-63B rev 4 AAL2. It also closes gap G1: when Valkey is down, sign-in now fails closed with `503` instead of crashing with `500`.

## What this phase delivers

- **Two-step sign-in.** `POST /auth/login` answers with a `status`: `mfa_required` (a pending session, 5 minutes, reaching only `/auth/mfa/*` and sign-out), `mfa_enrolment_required` (an account with no factor yet), or `signed_in` (only when MFA is switched off for a spec).
- **An enrolment gate.** An account without a factor reaches its account basics and enrolment; everything else answers `403` with `"mfa_enrolment_required": true`.
- **Authenticator apps (TOTP).** RFC 6238 codes, each usable once, with one step of clock drift either way.
- **Passkeys (WebAuthn).** Registration and sign-in ceremonies verified against this deployment's origin, with user verification (PIN or biometric) required.
- **Recovery codes.** Ten codes of 80 bits, shown once, single-use; regenerating replaces the set; using one emails the user.
- **Protection of the factors.** The last one cannot be removed; adding or removing one needs a sign-in within 15 minutes, emails the user and is audited without secrets; a password reset never bypasses MFA.
- **G1 closed.** A Valkey outage answers `503` with `Retry-After` on sign-in, on second factors and on reset requests, and never lets an attempt through uncounted.
- **Endpoints** (all under `/api/v1`):

| Method | Path | Session needed | Purpose |
| --- | --- | --- | --- |
| `POST` | `/auth/mfa/totp` | pending | Complete sign-in with an authenticator-app code |
| `POST` | `/auth/mfa/recovery-code` | pending | Complete sign-in with a recovery code |
| `POST` | `/auth/mfa/passkey/options` | pending | Start a passkey sign-in |
| `POST` | `/auth/mfa/passkey` | pending | Complete sign-in with a passkey |
| `GET` | `/account/mfa` | signed in, enrolled or not | Which factors the account has |
| `POST` | `/account/mfa/totp` | same, recent sign-in | Start adding an authenticator app |
| `POST` | `/account/mfa/totp/confirm` | same | Activate it with a code |
| `DELETE` | `/account/mfa/totp` | same, recent sign-in | Remove it |
| `POST` | `/account/mfa/passkeys/options` | same, recent sign-in | Start adding a passkey |
| `POST` | `/account/mfa/passkeys` | same, recent sign-in | Finish adding a passkey |
| `DELETE` | `/account/mfa/passkeys/{id}` | same, recent sign-in | Remove a passkey |
| `POST` | `/account/mfa/recovery-codes` | same, recent sign-in | Replace the recovery codes |

- **Changed endpoints.** `POST /auth/login` and `POST /auth/register` return `status` (and `methods` for `mfa_required`, without `user`). `GET /auth/session` adds `session.aal` and `mfa_enrolment_required`. Everything outside account basics now needs a full sign-in.
- **Migration** `0006`: four tables and two `sessions` columns (below).
- **Operator commands.** `purge-expired` also deletes spent and expired passkey challenges.
- **Settings.** None. The MFA policy is a `build_providers` keyword for tests, not an operator setting.

### Data model

| Table | Columns | Notes |
| --- | --- | --- |
| `sessions` (+2) | `aal` (1 or 2), `is_mfa_pending` | Checks: `aal IN (1, 2)`, and a pending session is aal 1. Existing sessions become aal 1, so their users must enrol. |
| `totp_credentials` | `id, user_id (unique), secret_sealed, created_at, confirmed_at, last_used_step` | One per account. The secret only sealed, under the user's data key. |
| `recovery_codes` | `id, user_id, code_hash, created_at, used_at` | `code_hash` is an HMAC, never the code. |
| `passkeys` | `id, user_id, credential_id (unique), public_key, sign_count, name, created_at, last_used_at, transports` | A credential id belongs to one account, ever. |
| `webauthn_challenges` | `id, session_id (cascade), user_id, purpose, challenge, created_at, expires_at, used_at` | Gone with its session. |

`data_keys` also gains a row per enrolled user (scope = the user's id), created at first enrolment.

## Architecture choices and why

### D6.1 Two-step sign-in through a pending session

- **Decision:** a correct password on an account that has a factor starts a session with `is_mfa_pending = true` and `aal = 1`, ending 5 minutes after it began, however active. Completing a factor revokes it (a conditional revoke: only once) and issues a new aal 2 session with new session and CSRF tokens. The login response for `mfa_required` lists the methods and does not include the user.
- **Why:** NIST AAL2 needs two factors before the session is trusted. Rotating the token at step-up means a token observed before the second factor — or planted by an attacker — never gains what the second factor grants (session fixation). The short life limits what a stolen password alone can hold.
- **Rejected:** one sign-in request carrying password and code together (the browser cannot know whether to ask for a code, or which kind, before the password is checked); upgrading the pending session in place (the same token would gain privileges); a signed "MFA ticket" cookie instead of a session (a second token type to store, revoke and audit).
- **Costs:** two round trips; one more session row per sign-in.
- **Where:** [`application/accounts.py`](../../services/api/app/application/accounts.py) (`login`, `new_session`), [`application/mfa.py`](../../services/api/app/application/mfa.py) (`_complete_sign_in`), [`domain/sessions.py`](../../services/api/app/domain/sessions.py).
- **Proven by:** `tests/test_mfa_api.py::TestTwoStepSignIn`, `tests/test_mfa_domain.py::TestSessionAssurance`.

### D6.2 Four gates instead of one

- **Decision:** routes choose one of four dependencies in [`api/session.py`](../../services/api/app/api/session.py): `AnySessionDep` (only sign-out), `PendingDep` (`/auth/mfa/*`), `AccountBasicsDep` (who am I, sessions, password, reauthenticate, `/account/mfa*`), and `CurrentDep` (everything else — admin, projects, search). A pending session elsewhere gets `401` with `mfa_required`; an un-enrolled one outside the basics gets `403` with `mfa_enrolment_required`.
- **Why:** deny by default. `CurrentDep` was already on every protected route, so making it mean "fully signed in" gated every existing and future route at once, including the projects routes built in parallel.
- **Rejected:** a middleware with an allowlist of paths (path strings drift from routes; a dependency is checked by the type system and the route inventory test); letting a pending session read `/auth/session` (it would reveal the account to someone holding only the password).
- **Costs:** a route author must pick the right gate; picking `CurrentDep` is the safe default.
- **Where:** [`api/session.py`](../../services/api/app/api/session.py), [`api/routes/auth.py`](../../services/api/app/api/routes/auth.py), [`api/routes/account.py`](../../services/api/app/api/routes/account.py), [`api/routes/mfa.py`](../../services/api/app/api/routes/mfa.py).
- **Proven by:** `TestTwoStepSignIn::test_a_pending_session_reaches_nothing_but_the_mfa_endpoints`, `tests/test_mfa_api.py::TestUntilEnrolled`.

### D6.3 MFA required by default, and not a setting

- **Decision:** `MfaPolicy(is_required=True)` is the default; `build_providers(mfa_policy=…)` is the only way to change it, and only tests do. An account that has a factor always gets the second step, whatever the policy says.
- **Why:** a setting is a knob an operator can turn by mistake, and one more thing production must validate. The only current need for "off" is specs of the password factor alone.
- **Rejected:** an environment setting `HUMANI_T_MFA_REQUIRED` (revisit with OIDC in M12, where an identity provider may already enforce MFA); defaulting the test harness to "off" (a new spec would silently skip MFA).
- **Costs:** the existing password specs opt out explicitly (`make_harness(mfa_policy=MfaPolicy(is_required=False))`).
- **Where:** [`domain/mfa.py`](../../services/api/app/domain/mfa.py) (`MfaPolicy`), [`api/dependencies.py`](../../services/api/app/api/dependencies.py).
- **Proven by:** `tests/test_mfa_api.py::TestMfaIsRequiredByDefault`.

### D6.4 TOTP in the domain, on the standard library

- **Decision:** RFC 6238 (over RFC 4226) is implemented in [`domain/mfa.py`](../../services/api/app/domain/mfa.py) with `hmac`, `hashlib` and `struct`: SHA-1, 6 digits, 30-second steps, one step of drift. Codes are compared in constant time, only exact ASCII digits are accepted, and the secret is 160 bits.
- **Why:** it is about fifteen lines, the RFC publishes test vectors, and keeping it in the domain puts the replay rule ("a step is used once, and only later steps after it") next to the code it governs. Every authenticator app assumes these parameters.
- **Rejected:** `pyotp` (a dependency for fifteen lines, and the domain may not import third-party code, so it would need a seam of its own); SHA-256 or 8 digits (stronger on paper, silently incompatible with common apps).
- **Costs:** we own the code — mitigated by the RFC vectors in the tests.
- **Proven by:** `tests/test_mfa_domain.py::TestTotpCodes` (RFC vectors), `::TestTotpVerification`, `::TestProvisioningUri`.

### D6.5 Single use, decided by the database

- **Decision:** a TOTP sign-in stores the step it used with `UPDATE … SET last_used_step = :step WHERE id = :id AND (last_used_step IS NULL OR last_used_step < :step)`; a recovery code is spent with `UPDATE … SET used_at WHERE … AND used_at IS NULL`; a challenge with `UPDATE … RETURNING`; a passkey counter with `UPDATE … WHERE sign_count < :new`. Each reports whether it won.
- **Why:** check-then-write lets two simultaneous requests carrying the same code both pass the check. Postgres row-locks the row for the conditional `UPDATE`, so the second waits, re-checks and loses ([`architecture.md` §5.7](../architecture.md#57-concurrency-and-resources)).
- **Rejected:** remembering used codes in Valkey (a second store to keep consistent, and lost when Valkey restarts).
- **Proven by:** `TestTotpIsSingleUse::test_two_simultaneous_sign_ins_with_one_code_have_one_winner`, `TestRecoveryCodes::test_two_simultaneous_uses_of_one_code_have_one_winner`, `tests/test_mfa_storage_contract.py::TestPostgresSingleUseUnderRaces`.

### D6.6 A data key per user, created at first enrolment

- **Decision:** the TOTP secret is sealed with the envelope encryption of [03](03-envelope-encryption.md) under the scope `user.id`, bound to `("totp_credentials.secret", credential id)`. Recovery codes are stored as HMAC-SHA256 under a key derived with HKDF-SHA256 from the same data key (`info = "recovery-codes/v1"`). The scope is created lazily, in the transaction of the first enrolment.
- **Why:** a database copy alone cannot read a secret or test a guessed recovery code; the KEK is needed too. Deriving the HMAC key from the user's data key adds no new deployment secret, and shredding the user's key ends both the secret and the codes.
- **Rejected:** a deployment-wide "pepper" secret for the HMAC (another secret to provision, rotate and back up); storing recovery codes with Argon2 (80 random bits need no slow hash; a keyed hash is enough and cheap); creating the scope at account creation (every account would get a key it may never use).
- **Costs:** every TOTP check decrypts; every recovery-code check derives a key. Both are microseconds.
- **Where:** [`application/mfa.py`](../../services/api/app/application/mfa.py), [`application/encryption.py`](../../services/api/app/application/encryption.py) (`ensure_scope`, `derive_key`), [`crypto/aesgcm.py`](../../services/api/app/crypto/aesgcm.py) (`derive_key`).
- **Proven by:** `TestTotpEnrolment::test_the_secret_is_stored_only_encrypted_under_the_users_own_key`, `TestRecoveryCodes::test_codes_are_stored_only_as_keyed_hashes`, `…::test_codes_die_with_the_users_data_key`.

### D6.7 Recovery codes people can type

- **Decision:** ten codes, each 16 Crockford base32 symbols (80 bits) shown as `XXXX-XXXX-XXXX-XXXX`. Input ignores case, spaces and dashes, and reads O as 0 and I or L as 1. Issued with the first factor, shown once, replaced as a set, counted, and emailed about when used.
- **Why:** NIST asks at least 64 bits for look-up secrets; the alphabet avoids characters misread from paper.
- **Rejected:** fewer, longer codes (harder to type); hex (more symbols for the same entropy).
- **Proven by:** `tests/test_mfa_domain.py::TestRecoveryCodes`, `tests/test_mfa_api.py::TestRecoveryCodes`.

### D6.8 Passkeys through py_webauthn, bound to `public_origin`

- **Decision:** a `PasskeyVerifier` Protocol in [`auth/base.py`](../../services/api/app/auth/base.py), implemented in [`auth/webauthn.py`](../../services/api/app/auth/webauthn.py), the only module that imports `webauthn`. The relying party id is the host of `public_origin`; the expected origin is `public_origin` itself. User verification is required, attestation is `"none"`, ES256 and the library's other algorithms are offered. The user handle is the account id (16 random bytes, no personal data). Every library failure becomes one `PasskeyVerificationError`.
- **Why:** WebAuthn verification is subtle (CBOR, COSE keys, flags, origins); a maintained library is safer than our own. Binding to `public_origin` means a passkey made for this deployment works nowhere else, and a phishing page on another origin gets nothing usable.
- **Rejected:** writing the verification ourselves; requiring attestation (it identifies authenticator models, adds certificate chains, and the brief has no need to restrict models).
- **Costs:** one dependency with three transitive ones (below).
- **Proven by:** `tests/test_mfa_api.py::TestPasskeyRegistration` and `::TestPasskeySignIn`, using [`tests/webauthn_authenticator.py`](../../services/api/tests/webauthn_authenticator.py): a software authenticator with a real P-256 key, real signatures and real CBOR. Each forgery test changes exactly one field (origin, RP id, user-verified flag, ceremony type, challenge, signature, counter).

### D6.9 Challenges: on the server, per session, spent by any answer

- **Decision:** every ceremony's challenge (32 random bytes) is stored against the session and purpose, lives 5 minutes, and is spent by the first answer, before verification, whether it succeeds or fails. Asking for options again retires the previous challenge.
- **Why:** a challenge that survives a failed attempt can be retried; one bound to a session cannot be answered from another browser.
- **Rejected:** a signed, stateless challenge (cannot be made single-use without storage).
- **Proven by:** `TestPasskeySignIn::test_a_challenge_is_spent_by_any_attempt_even_a_failed_one`, `TestPasskeyRegistration::test_a_challenge_issued_to_one_session_is_refused_in_another`, `tests/test_mfa_storage_contract.py::TestWebAuthnChallenges`.

### D6.10 Sign counters catch clones

- **Decision:** a sign-in is accepted only if the authenticator's counter is higher than the stored one, or both are zero (authenticators that do not count). The comparison is in the domain and repeated in the conditional `UPDATE`.
- **Why:** WebAuthn §6.1.1: a cloned key and the original cannot both keep a counter advancing.
- **Proven by:** `TestPasskeySignIn::test_a_cloned_authenticator_is_caught_by_its_counter`, `tests/test_mfa_domain.py::TestPasskeySignCount`.

### D6.11 Changing authenticators: serialised, protected, announced

- **Decision:** every add or remove first saves the user row with its version check, then reads what is enrolled, then writes. The last factor cannot be removed (`409`). The first factor issues recovery codes, ends every other session of the account and moves this browser to aal 2 under a new token. Adding, removing and regenerating need a sign-in within 15 minutes, email the user after the commit, and are audited.
- **Why:** without the row lock, two simultaneous removals each see "two left" and leave none. Ending other sessions at first enrolment stops a password-only session opened earlier from outliving the account gaining a factor.
- **Rejected:** a count check alone (lost under races); allowing zero factors (MFA would be one request away from off).
- **Proven by:** `tests/test_mfa_api.py::TestRemovingAuthenticators` (including `test_removing_the_last_two_at_once_leaves_one`), `::TestMfaAuditTrail`.

### D6.12 Second factors throttled like passwords

- **Decision:** TOTP and recovery-code attempts share a counter per account (`mfa:account:<id>`): 10 per 15 minutes, counted before checking, reset on success. While throttled even the right code is refused.
- **Why:** per account, so starting a fresh pending session (which needs the password again) buys no extra guesses. Six digits with three valid steps gives about a 1 in 333,000 chance per guess.
- **Proven by:** `tests/test_mfa_api.py::TestSecondFactorThrottling`.

### D6.13 The rate limiter fails closed (G1)

- **Decision:** `ValkeyRateLimiter` uses 2-second socket timeouts and no client retries, and turns any Redis or socket error into `RateLimiterUnavailableError`, which the API answers with `503` and `Retry-After`. The attempt is refused before the password is verified or a code is spent.
- **Why:** a limiter that cannot count must not let attempts through, and a sign-in that hangs on a dead Valkey is worse than a clear `503`.
- **Rejected:** falling back to an in-process limiter (each worker would allow its own full quota); letting the error reach the generic handler (`500`, and without request id or security headers — see [D2.11](02-configuration-and-persistence.md)).
- **Proven by:** `tests/test_mfa_api.py::TestRateLimiterOutage` (one test uses a real unreachable Valkey address).

### D6.14 Resets and password changes keep MFA

- **Decision:** a password reset changes the password only; the next sign-in still needs the second factor. A password change rotates the session and keeps its aal.
- **Proven by:** `tests/test_mfa_api.py::TestPasswordResetDoesNotBypassMfa`, `TestTwoStepSignIn::test_changing_the_password_keeps_the_session_at_aal2`.

### Deliberately not used

- **SMS or email codes:** phishable, interceptable, and NIST restricts them.
- **Passwordless passkey sign-in:** out of scope for M9; a passkey here is always the second factor.
- **"Remember this device":** every sign-in asks for the second factor.

## Libraries

### webauthn (py_webauthn) 3.0.1

- **What it does:** builds WebAuthn options and verifies registration and authentication responses: CBOR and COSE parsing, flags, origin, RP id hash, challenge, signature and counter.
- **Why this one:** maintained by Duo Labs, widely used, typed, and verifies exactly the spec's steps; its options serialise to the W3C JSON format browsers parse directly.
- **Alternatives:** `fido2` (python-fido2; broader, lower-level, more to hold correctly); our own implementation.
- **Trade-offs:** pulls in `cbor2`, `pyOpenSSL` and `pyasn1`/`pyasn1-modules` (used for attestation formats we do not request). Contained by importing it in one module behind a Protocol and forbidding it elsewhere with import-linter.
- **What we gave up:** nothing we need; attestation verification is available if ever wanted.

## Files to read, in order

Start from the rules, then follow a sign-in request outward.

1. [`app/domain/mfa.py`](../../services/api/app/domain/mfa.py) — TOTP, recovery codes, the counter rule, `MfaPolicy`, `MfaEnrolment`, and the errors.
2. [`app/domain/sessions.py`](../../services/api/app/domain/sessions.py) — `aal`, `is_mfa_pending`, the 5-minute pending lifetime.
3. [`app/storage/base.py`](../../services/api/app/storage/base.py) — the four new repositories and what each conditional write promises.
4. [`app/application/accounts.py`](../../services/api/app/application/accounts.py) — `login` choosing between a pending and an aal 1 session; `new_session`.
5. [`app/application/mfa.py`](../../services/api/app/application/mfa.py) — every second-factor use case; read the module docstring first.
6. [`app/auth/webauthn.py`](../../services/api/app/auth/webauthn.py) — the only place WebAuthn is verified.
7. [`app/api/session.py`](../../services/api/app/api/session.py) — the four gates.
8. [`app/api/routes/mfa.py`](../../services/api/app/api/routes/mfa.py) — where cookies are replaced after step-up.
9. [`app/ratelimit/valkey.py`](../../services/api/app/ratelimit/valkey.py) and [`app/api/errors.py`](../../services/api/app/api/errors.py) — G1.
10. [`tests/webauthn_authenticator.py`](../../services/api/tests/webauthn_authenticator.py) — what a browser and a security key actually send.

## Set up

Everything from [00 — Setup](00-setup.md), with the admin created. Then apply the new migration and restart the API:

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

You also need:

- **An authenticator app on a phone:** Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, or a password manager with TOTP. The phone's clock must be set automatically.
- **Postman:** add the cookie-capture script from [S0.10](00-setup.md#s010-postman) to the Post-response tab of `POST {{base}}/account/mfa/totp/confirm`, `POST {{base}}/account/mfa/passkeys`, and every `POST {{base}}/auth/mfa/*` request: each of them can issue new cookies. Also add the cookie-capture script to the Post-response tab of `POST {{base}}/account/password` and of `POST {{base}}/auth/reauthenticate`: a password change and a step-up both issue new cookies and revoke the old session. (If you set up Postman from [S0.10](00-setup.md#s010-postman) or for [05](05-accounts-and-sessions.md#set-up), it is already there.)
- **For passkeys only:** a browser (Chrome 129+, Edge, Firefox 119+ or Safari 18+). Browsers refuse an IP address as a relying party id, so the passkey tests use `localhost`; see [T6.8](#t68-a-passkey-in-a-real-browser).

Every account now starts un-enrolled. The admin from 00 will be asked to enrol on first sign-in.

## Manual tests

### T6.1 The enrolment gate

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T6.1.1 | `POST {{base}}/auth/login` as the admin | `200`, `{"status":"mfa_enrolment_required","user":{…}}`; cookies captured | An account with no factor is signed in at aal 1 (D6.2) | `"status":"signed_in"`: the policy is off — check `build_providers` |
| T6.1.2 | `GET {{base}}/auth/session` | `200`; `session.aal` is `1`, `mfa_enrolment_required` is `true` | | |
| T6.1.3 | `POST {{base}}/admin/invitations` `{"email":"kofi@example.org"}` | `403`, `"mfa_enrolment_required": true` | Everything beyond the basics is closed (D6.2) | `201`: the admin route is not on `CurrentDep` |
| T6.1.4 | `GET {{base}}/account/sessions` and `GET {{base}}/account/mfa` | both `200`; `is_enrolled` `false`, `recovery_codes_remaining` `0` | The basics stay reachable | `403`: the route uses `CurrentDep` instead of `AccountBasicsDep` |

### T6.2 Enrolling an authenticator app on a phone

- **Do:**
  1. `POST {{base}}/account/mfa/totp` (body `{}`), within 15 minutes of signing in.
  2. In the phone app choose **Enter a setup key** (or similar), account name `humani-t admin`, key = the `secret` from the response, type **time-based**. Do not paste the secret or the `otpauth_uri` into any online QR generator: that would send the secret to a third party. To scan instead of typing, render the URI locally, for example with a password manager that accepts `otpauth://` links.
  3. `POST {{base}}/account/mfa/totp/confirm` with `{"code":"<the six digits on the phone>"}`.
  4. `GET {{base}}/auth/session`, then `GET {{base}}/account/mfa`, then repeat `POST {{base}}/admin/invitations`.
- **Expect:**
  1. `201`, `{"secret":"…32 characters…","otpauth_uri":"otpauth://totp/humani-t:admin%40example.org?secret=…&issuer=humani-t&algorithm=SHA1&digits=6&period=30"}`.
  3. `200`, `{"recovery_codes":[10 codes like "7K3M-QX9P-…"]}` and two new `set-cookie` headers. **Copy the ten codes somewhere safe now**; they are never shown again. In Mailpit, "A sign-in method was added to your humani-t account".
  4. `session.aal` is `2`, `mfa_enrolment_required` is `false`; `totp` is present and no `secret` appears anywhere; the invitation now gets `201`.
- **Proves:** D6.4, D6.6, D6.7, D6.11: the secret is active only after a valid code, step-up rotates the session, recovery codes come with the first factor.
- **If it fails:** a `400` on confirm with the right code usually means the phone's clock is off by more than 30 seconds; a `200` without new cookies means step-up did not rotate.

### T6.3 What the database holds

- **Do:** in pgAdmin (database `humani_t`):

```sql
SELECT user_id, length(secret_sealed) AS sealed_bytes, confirmed_at, last_used_step FROM totp_credentials;
SELECT user_id, encode(code_hash, 'hex') AS code_hash, used_at FROM recovery_codes ORDER BY created_at LIMIT 3;
SELECT scope_id, kek_version, destroyed_at FROM data_keys WHERE scope_id IN (SELECT id FROM users);
SELECT id, aal, is_mfa_pending, revoked_at FROM sessions ORDER BY created_at DESC LIMIT 5;
```

- **Expect:** `sealed_bytes` about 49 (version, nonce, 20 bytes, tag) and no readable secret; `last_used_step` set from the confirmation. Recovery code hashes are 64 hex characters that match none of your codes. The admin's id appears in `data_keys`. The newest session is `aal 2`; the enrolment-time session above it is revoked.
- **Proves:** D6.6 (encrypted under the user's own key, keyed hashes) and D6.11 (other sessions ended at first enrolment).
- **If it fails:** a readable secret or a code in `recovery_codes` is critical: stop and report.

### T6.4 Two-step sign-in with the phone

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T6.4.1 | `POST {{base}}/auth/login` as the admin | `200`, `{"status":"mfa_required","methods":["totp","recovery_code"]}`, **no** `user`; cookies captured | Password yields only a pending session (D6.1) | `user` present: the response leaks the account before the second factor |
| T6.4.2 | `GET {{base}}/auth/session` | `401`, `"mfa_required": true` | A pending session reaches nothing else | `200`: the gate is missing |
| T6.4.3 | `POST {{base}}/account/mfa/totp` | `401`, `"mfa_required": true` | A pending session cannot enrol a new factor | `201` would let a password alone add an attacker's phone |
| T6.4.4 | `POST {{base}}/auth/mfa/totp` with a wrong code such as `000000` | `401`, "The code or passkey was not accepted." | | |
| T6.4.5 | Copy `{{session}}`, then `POST {{base}}/auth/mfa/totp` with the phone's current code | `200`, `{"status":"signed_in","user":{…}}`; new cookies | Step-up issues a new token | |
| T6.4.6 | `GET {{base}}/auth/session` | `200`, `session.aal` `2` | | |
| T6.4.7 | Sign in again (T6.4.1) and send **the same code** as in T6.4.5 before the phone changes it | `401` | A code works once (D6.5) | `200`: replay is possible |
| T6.4.8 | Send `GET {{base}}/auth/session` with the old pending token from T6.4.5 in the `Cookie` header | `401` | The pending token died at step-up | |

### T6.5 The pending session is short

- **Do:** sign in (T6.4.1), wait 5 minutes and 10 seconds, then `POST {{base}}/auth/mfa/totp` with a fresh, correct code.
- **Expect:** `401`, "Sign in to continue." Signing in again works.
- **Proves:** D6.1: a stolen password holds a pending session for 5 minutes at most.
- **If it fails:** a `200` means the pending lifetime is not applied in `Session.is_active`.

### T6.6 Recovery codes

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T6.6.1 | Sign in (pending), then `POST {{base}}/auth/mfa/recovery-code` with one of your codes typed in lower case without dashes | `200`, `signed_in` | Codes are forgiving to type (D6.7) |
| T6.6.2 | Mailpit | "A recovery code was used to sign in to your humani-t account", saying 9 unused codes remain, and not containing the code | The owner hears about it |
| T6.6.3 | Sign in again and send the same code | `401` | Single use |
| T6.6.4 | Signed in, `GET {{base}}/account/mfa` | `recovery_codes_remaining` `9` | |
| T6.6.5 | `POST {{base}}/account/mfa/recovery-codes` | `201`, ten new codes; Mailpit: "New recovery codes…"; an old unused code now gets `401` at sign-in | Regenerating replaces the set |

Keep the new codes: T6.10 and T6.11 need a way in.

### T6.7 Throttling second factors

- **Do:** sign in (pending), send ten wrong codes to `POST {{base}}/auth/mfa/totp`, then the right one.
- **Expect:** the eleventh answers `429` with a `Retry-After` header, even though the code is right. After 15 minutes (or `docker compose restart valkey` in development) it works again.
- **Proves:** D6.12.
- **If it fails:** a `200` means attempts are not counted before checking.

### T6.8 A passkey in a real browser

There is no web page yet (M13), so these steps drive the API from the browser's developer console, exactly as the web app will. Browsers accept `localhost` but never an IP address as a relying party, so the API must run as `http://localhost:8731` for this test.

1. Stop the API. In `services\api\.env`, change `HUMANI_T_PUBLIC_ORIGIN` to `http://localhost:8731` (note the old value). Start the API again.
2. Open `http://localhost:8731/docs` in the browser (the address bar must say `localhost`), then open the developer console (F12 → Console).
3. Paste this helper; it sends the CSRF header the way the web app will:

```js
const api = async (path, body) => {
  const csrf = (document.cookie.match(/__Host-csrf=([^;]+)/) || [])[1] || "";
  const r = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body ?? {}),
  });
  return { status: r.status, body: r.status === 204 ? null : await r.json() };
};
```

4. Sign in and complete the TOTP step (use your real admin password and the phone's code):

```js
await api("/auth/login", { email: "admin@example.org", password: "<admin password>" });
await api("/auth/mfa/totp", { code: "<six digits>" });
```

5. Register a passkey; the browser asks for your PIN, fingerprint or face:

```js
const { body: { options } } = await api("/account/mfa/passkeys/options");
const created = await navigator.credentials.create({ publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(options) });
await api("/account/mfa/passkeys", { name: "This laptop", credential: created.toJSON() });
```

- **Expect:** step 4: `{status: 200, body: {status: "mfa_required", …}}` then `{status: 200, body: {status: "signed_in", …}}`. Step 5: the options show `rp.id: "localhost"`, `authenticatorSelection.userVerification: "required"`; the browser prompt names `humani-t`; the last call returns `{status: 201, body: {passkey: {name: "This laptop", …}, recovery_codes: null}}` (not the first factor, so no new codes). Mailpit: "A sign-in method was added…".
- **Proves:** D6.8: a real authenticator's attestation verifies against this origin.
- **If it fails:** `NotAllowedError` in the browser usually means the address is not `localhost` or the prompt was cancelled; a `400` from the API with a correct prompt means origin or RP id disagree — check `HUMANI_T_PUBLIC_ORIGIN` and the address bar match exactly, including the port. If cookies do not stick on `http://localhost` in your browser, use Chrome.

### T6.9 Signing in with the passkey

- **Do:** in the same console:

```js
await api("/auth/logout");
await api("/auth/login", { email: "admin@example.org", password: "<admin password>" });
const { body: { options: request } } = await api("/auth/mfa/passkey/options");
const assertion = await navigator.credentials.get({ publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(request) });
await api("/auth/mfa/passkey", { credential: assertion.toJSON() });
await api("/auth/mfa/passkey", { credential: assertion.toJSON() });
```

- **Expect:** login lists `"passkey"` first in `methods`; the first `/auth/mfa/passkey` call returns `200` with `signed_in`; the immediate replay of the same assertion returns `401` (the pending session is gone, and so is the challenge). `GET /account/mfa` shows a `last_used_at` on the passkey.
- **Proves:** D6.8, D6.9: the ceremony verifies, and an assertion cannot be replayed.
- **If it fails:** a `200` on the replay is critical: challenges or pending sessions are reusable.
- **Afterwards:** restore `HUMANI_T_PUBLIC_ORIGIN` in `.env` to its old value and restart the API.

### T6.10 Removing factors

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T6.10.1 | Signed in as an account with only TOTP (for example a new invitee who enrolled TOTP), `DELETE {{base}}/account/mfa/totp` | `409`, "This is your only second factor…" | The last factor is protected (D6.11) |
| T6.10.2 | As the admin (TOTP and passkey), wait 16 minutes, `GET {{base}}/auth/session`, then `DELETE {{base}}/account/mfa/totp` | `401`, `reauthentication_required: true` | Removing needs a recent sign-in |
| T6.10.3 | `POST {{base}}/auth/reauthenticate` with the password (its cookie-capture script stores the new session), then the `DELETE` again | `204` with new cookies, then `204`; Mailpit: "A sign-in method was removed…" | |
| T6.10.4 | `DELETE {{base}}/account/mfa/passkeys/{id}` for the remaining passkey | `409` | |
| T6.10.5 | Sign in: `methods` | `["passkey","recovery_code"]`, no `totp` | A removed factor no longer signs in |

To use Postman as the admin afterwards, add TOTP again (T6.2).

### T6.11 A password reset does not bypass MFA

- **Do:** request a reset for the admin (`POST {{base}}/auth/password-reset`), confirm it with the token from Mailpit and a new password, then sign in with the new password.
- **Expect:** the confirm returns `204` with no `set-cookie`; the sign-in returns `"status":"mfa_required"`.
- **Proves:** D6.14.
- **If it fails:** `signed_in` here means a reset link is a way around the second factor — critical.

### T6.12 Valkey down: sign-in fails closed (G1)

- **Do:** `docker compose -f ..\..\compose.yaml stop valkey`, then `POST {{base}}/auth/login`; then `docker compose -f ..\..\compose.yaml start valkey` and sign in again.
- **Expect:** within about 2 seconds, `503`, `content-type: application/problem+json`, `Retry-After: 5`, an `x-request-id` header, and the API log line `rate_limiter_unavailable`. After starting Valkey, sign-in works.
- **Proves:** D6.13: no `500`, no hang, no attempt let through uncounted.
- **If it fails:** a `500` means a Redis error reaches the generic handler; a long hang means the socket timeouts are missing.

### T6.13 What the audit log recorded

- **Do:** in pgAdmin:

```sql
SELECT sequence, action, target_type, actor_id, occurred_at FROM audit_events
WHERE action LIKE 'auth.mfa%' OR action LIKE 'auth.login%' OR action LIKE 'account.%' ORDER BY sequence DESC LIMIT 30;
```

- **Expect:** `auth.login_pending_mfa`, `auth.mfa_failed`, `auth.mfa_totp_succeeded`, `auth.mfa_recovery_code_succeeded`, `auth.mfa_passkey_succeeded`, `account.totp_added`, `account.passkey_added`, `account.recovery_codes_generated`, `account.totp_removed`. No secret, code or email address in any column.
- **Proves:** D6.11 audit, and that provenance is captured when the decision is made.

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T6.1 | ☐ | |
| T6.2 | ☐ | |
| T6.3 | ☐ | |
| T6.4 | ☐ | |
| T6.5 | ☐ | |
| T6.6 | ☐ | |
| T6.7 | ☐ | |
| T6.8 | ☐ | |
| T6.9 | ☐ | |
| T6.10 | ☐ | |
| T6.11 | ☐ | |
| T6.12 | ☐ | |
| T6.13 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| MFA is required by default; the policy is explicit | `tests/test_mfa_api.py::TestMfaIsRequiredByDefault` |
| An un-enrolled account reaches only its basics and enrolment | `tests/test_mfa_api.py::TestUntilEnrolled` |
| Password then factor; pending sessions reach only `/auth/mfa/*`; step-up rotates the token | `tests/test_mfa_api.py::TestTwoStepSignIn` |
| TOTP per RFC 6238, with its vectors | `tests/test_mfa_domain.py::TestTotpCodes`, `::TestTotpVerification` |
| TOTP enrolment: secret once, encrypted under the user's key, confirmed before active | `tests/test_mfa_api.py::TestTotpEnrolment` |
| A TOTP code works once, also under races | `tests/test_mfa_api.py::TestTotpIsSingleUse` |
| Second factors are throttled per account | `tests/test_mfa_api.py::TestSecondFactorThrottling` |
| Recovery codes: entropy, keyed hashes, single use, regenerate, email, shredding | `tests/test_mfa_domain.py::TestRecoveryCodes`, `tests/test_mfa_api.py::TestRecoveryCodes` |
| Passkey registration and sign-in verify real ceremonies; forgeries fail | `tests/test_mfa_api.py::TestPasskeyRegistration`, `::TestPasskeySignIn` |
| Last factor protected, also under races; recent sign-in; emails | `tests/test_mfa_api.py::TestRemovingAuthenticators` |
| A password reset does not bypass MFA | `tests/test_mfa_api.py::TestPasswordResetDoesNotBypassMfa` |
| Every change and every sign-in step is audited, without secrets | `tests/test_mfa_api.py::TestMfaAuditTrail` |
| Valkey down: 503, fail closed (G1) | `tests/test_mfa_api.py::TestRateLimiterOutage` |
| Storage: single use and conditional writes, memory and Postgres | `tests/test_mfa_storage_contract.py` |
| The test instruments themselves are sound | `tests/test_mfa_api.py::TestTheTestInstruments` |

How to run only this phase's tests (the Postgres ones need `TEST_DATABASE_URL`, see [00](00-setup.md)):

```powershell
uv run pytest tests/test_mfa_domain.py tests/test_mfa_api.py tests/test_mfa_storage_contract.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G6.1 | Passkeys are tested with a software authenticator, not a browser's | A browser quirk would surface only in manual tests | M13: Playwright with a virtual WebAuthn authenticator |
| G6.2 | Attestation is not checked (`"none"`) | Any authenticator model is accepted | Revisit only if an operator must restrict models |
| G6.3 | An account that loses every factor and every recovery code cannot recover by itself | It needs an administrator, and there is no command for that yet | An audited operator command, later |
| G6.4 | The in-memory store does not isolate concurrent transactions | The race specs prove one winner in memory only because each use case writes in a single transaction; the real guarantee is the Postgres contract suite | Run the Postgres suite in CI (M4) |
| G6.5 | No QR code is rendered by the API | Enrolment needs typing the key, or a local QR tool | The web app shows a QR code (M13) |

## Glossary

| Term | Meaning |
| --- | --- |
| **AAL** | Authenticator assurance level (NIST 800-63B). Here, aal 1 after a password, aal 2 after a password and a second factor. |
| **Pending session** | A session past the password and waiting for its second factor; it lasts 5 minutes and reaches only `/auth/mfa/*` and sign-out. |
| **Step-up** | Raising a session's assurance level; here it always replaces the session with a new one. |
| **TOTP** | Time-based one-time password (RFC 6238): a 6-digit code from a shared secret and the current 30-second time step. |
| **Time step** | The number of 30-second intervals since 1970; each TOTP code belongs to one. |
| **Drift** | How far the phone's clock may differ from the server's: one step either way here. |
| **Replay** | Sending a captured code or response again. Stopped here by storing what was used and accepting it once. |
| **Recovery code** | A single-use fallback code for when the phone or passkey is lost. |
| **Passkey** | A WebAuthn credential: a key pair kept by a device or password manager; the server keeps only the public key. |
| **WebAuthn** | The W3C standard browsers use to create and use passkeys. |
| **Relying party (RP), RP id** | The website a passkey belongs to; its id is the host name (here, the host of `public_origin`). |
| **Ceremony** | One WebAuthn exchange: registration (create) or authentication (get). |
| **Challenge** | Random bytes the server issues for one ceremony, which the authenticator signs; single use. |
| **User verification** | The authenticator checking the person (PIN, fingerprint, face), not just their presence. |
| **Attestation** | An authenticator's statement of its make and model; not requested here (`"none"`). |
| **Sign counter** | A number the authenticator increases with every use; a value that does not increase suggests a clone. |
| **HMAC** | A keyed hash: without the key, a stored value cannot be checked against a guess. |
| **HKDF** | A function that derives independent keys for different purposes from one key. |
| **Fail closed** | When a safety check cannot run, refuse rather than allow. |
