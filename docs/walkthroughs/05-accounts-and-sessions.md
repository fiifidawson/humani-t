# 05 — Accounts and sessions

> **Milestones:** M8, plus the concurrency and resource-safety pass · **Status:** done · **Previous:** [04 — Audit log](04-audit-log.md) · **Next:** `06-mfa` (planned)

This phase lets people in, and keeps the wrong people out. An administrator is created from the command line and invites others by email; an invitee registers from the emailed link. Passwords follow NIST SP 800-63B rev 4 and are hashed with Argon2id off the event loop. Sessions live on the server and travel in `__Host-` cookies; every signed-in write must prove it came from our own page in three independent ways. Sign-in and reset attempts are throttled, counted before the slow check. Passwords can be changed (after a recent sign-in) and reset by email. Every one of these actions is written to the audit log ([04](04-audit-log.md)). A final pass made it all hold up under concurrent load: no connection is held across hashing or email, single-use links are single-use under races, and no request can silently overwrite another's change. It implements [`architecture.md` §5.1](../architecture.md#51-sign-in), [§5.2](../architecture.md#52-sessions), [§5.3](../architecture.md#53-request-integrity) and [§5.7](../architecture.md#57-concurrency-and-resources), and brief rule 6 (without MFA, which is phase 06).

## What this phase delivers

- **Endpoints** (all under `/api/v1`):

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/login` | anyone | Sign in; sets cookies |
| POST | `/auth/logout` | signed in | End this session |
| GET | `/auth/session` | signed in | Who is signed in |
| POST | `/auth/reauthenticate` | signed in | Confirm password for sensitive actions |
| POST | `/auth/register` | invitee | Accept an invitation; signs in |
| POST | `/auth/password-reset` | anyone | Email a reset link (always 202) |
| POST | `/auth/password-reset/confirm` | link holder | Set a new password |
| GET | `/account/sessions` | signed in | List own sessions |
| DELETE | `/account/sessions/{session_id}` | signed in | Sign out one device |
| POST | `/account/sessions/revoke-others` | signed in | Sign out every other device |
| POST | `/account/password` | signed in, recent | Change password |
| POST | `/admin/invitations` | admin | Invite by email |
| POST | `/admin/users/{user_id}/disable` | admin | Disable an account |
| POST | `/admin/users/{user_id}/enable` | admin, recent | Let a disabled account sign in again |

- **Seams:** `PasswordHasher` (`Argon2PasswordHasher`, `FakePasswordHasher`), `EmailProvider` (`SmtpEmailProvider`, `RecordingEmailProvider`), `RateLimiter` (`ValkeyRateLimiter`, `MemoryRateLimiter`).
- **Domain:** `PasswordPolicy` and `normalize_password` (`domain/passwords.py`); `Session`, `SessionPolicy`, `new_token`, `hash_token`, `tokens_match` (`domain/sessions.py`); `User`, `Invitation`, `PasswordReset`, `normalize_email` and the account errors (`domain/accounts.py`); `EmailMessage`, `EmailDeliveryError` (`domain/notifications.py`); `ConcurrentUpdateError`, `StorageUnavailableError` (`domain/errors.py`).
- **Use cases:** `AccountService` (`application/accounts.py`).
- **Migrations:** `0003` (users, sessions, invitations, password resets) and `0004` (`users.version` for optimistic locking).
- **Operator commands:** `create-admin` (password from standard input only) and `purge-expired`.
- **Settings:** `VALKEY_URL`, `SMTP_*` ([settings reference](02-configuration-and-persistence.md#settings-reference)).
- **Fixed in code** (deliberately not configurable yet): session idle 1 h, absolute 24 h, re-authentication window 15 min; invitation 7 days; reset link 30 min; 10 sign-in attempts per account and 100 per client, 5 reset requests per email, each per 15 min; 4 concurrent password hashes; passwords 15–1024 characters (the HTTP boundary caps input at 4096); tokens of 32 random bytes; user agents stored up to 256 characters.

### Data model

| Table | Columns | Purpose and rules |
| --- | --- | --- |
| `users` | `id, email, display_name, password_hash, is_admin, is_disabled, created_at, version` | One row per account. `email` is unique and must be lower-case (check `email_is_normalised`). `version` increases on every save; a save with a stale version is refused. |
| `sessions` | `id, user_id, token_hash, csrf_token_hash, created_at, last_seen_at, authenticated_at, revoked_at, user_agent` | One row per signed-in browser. Only hashes of tokens (`token_hash` unique). Updates are conditional on `revoked_at IS NULL`. |
| `invitations` | `id, email, token_hash, invited_by, created_at, expires_at, accepted_at` | Single-use, 7 days. Claimed with a conditional update. |
| `password_resets` | `id, user_id, token_hash, created_at, expires_at, used_at` | Single-use, 30 minutes; a newer link cancels older ones. |

## Architecture choices and why

### D5.1 Password rules from NIST SP 800-63B rev 4

- **Decision:** at least 15 characters, at most 1024, counted in characters after Unicode NFKC normalisation; no composition rules ("must include a symbol"); no forced rotation. A password is refused (`PasswordRejectedError`, `422` with `violations`) if it is `too_short`, `too_long`, `too_common` — in the built-in blocklist (about fifty entries such as `password`, `qwerty`, `humanitarian`), a repetition of a unit of four characters or fewer (`aaaaaaaaaaaaaaa`, `abcabcabc…`) or of a blocklisted word, or any slice of a keyboard row or counting run — or `contains_personal_details`: any run of four or more letters or digits from the user's own email or display name. All violations are reported together. Passwords are normalised with NFKC before hashing and before verifying.
- **Why:** NIST's current guidance: length and screening work; composition rules and rotation make passwords more predictable. 15 is the minimum when a password may be the only factor (it is, until MFA in phase 06). NFKC means the same password typed on any keyboard hashes the same.
- **Rejected:** "8 characters with a symbol" rules; periodic expiry; checking Have I Been Pwned by default (an outbound call; opt-in only, per the plan).
- **Costs:** the built-in screening list is small; a deployment can pass a larger one (`PasswordPolicy(blocklist=…)`). Users used to "8 characters with a symbol" may be surprised.
- **Where:** [`app/domain/passwords.py`](../../services/api/app/domain/passwords.py).
- **Proven by:** `tests/test_passwords.py::TestPasswordPolicy`, `TestNormalisation`.

### D5.2 Argon2id, off the event loop, with a concurrency cap

- **Decision:** Argon2id with RFC 9106's second recommended profile (`RFC_9106_LOW_MEMORY`: 64 MiB memory, 3 passes, 4 lanes, 16-byte salt, 32-byte hash). Hashing and verifying run in a worker thread (`asyncio.to_thread`), at most 4 at a time (an `asyncio.Semaphore`). A hash made with weaker parameters (`needs_rehash`) is upgraded at the next successful sign-in, and the upgrade is skipped harmlessly if the user changed meanwhile. A malformed stored hash verifies as `False` and never raises.
- **Why:** Argon2id won the Password Hashing Competition and is OWASP's first recommendation; memory-hardness defeats GPU cracking. Running it in a thread keeps one slow hash from freezing every other request. The cap stops a burst of sign-ins using `64 MiB × n` of memory.
- **Rejected:** bcrypt (not memory-hard, 72-byte input limit); PBKDF2 (fast on GPUs); scrypt (fine, but Argon2id is the current recommendation).
- **Costs:** each sign-in costs tens of milliseconds and 64 MiB briefly — that is the point. Tests use `FakePasswordHasher` (a real but cheap SHA-256 that counts verifications) so the suite stays fast.
- **Where:** [`app/auth/argon2.py`](../../services/api/app/auth/argon2.py), `AccountService._hash` / `_verify`.
- **Proven by:** `tests/test_passwords.py::TestArgon2PasswordHasher`, `TestFakePasswordHasher`.

### D5.3 Server-side sessions, not JWTs

- **Decision:** a session is a row in Postgres. The browser holds an opaque 256-bit random token (`secrets.token_urlsafe(32)`); the database holds only its SHA-256. A second random token, the CSRF token, is stored the same way (`csrf_token_hash`). `authenticate` resolves a cookie in its own short transaction and extends the idle timer (`touch`); it returns `None` for every way a token can be unusable — unknown, revoked, expired, or belonging to a disabled account — so callers cannot leak which.
- **Why:** a session can be listed, revoked instantly and audited. A JWT cannot be revoked before it expires without building a server-side list anyway. Hashing the token means a copy of the database cannot be used to sign in as anyone. A plain SHA-256 is enough, rather than a password hash, because the token already has 256 bits of randomness.
- **Rejected:** JWTs in local storage (readable by any injected script); JWTs in cookies (still not revocable).
- **Costs:** one database read and one small write per authenticated request.
- **Proven by:** `tests/test_sessions.py::TestTokens`, `tests/test_auth_api.py::TestSessionCookie::test_a_session_token_is_never_stored_in_the_clear`, `TestCurrentSession`.

### D5.4 Cookie design

- **Decision:** `__Host-session` (`Secure; HttpOnly; SameSite=Lax; Path=/`, no `Domain`) carries the session token. `__Host-csrf` (`Secure; SameSite=Strict; Path=/`, readable by scripts) carries the CSRF token. Neither has `Max-Age`, so both end with the browser session; the server-side timeouts end them sooner. Logout clears both with the same attributes and `Max-Age=0` (browsers ignore a deletion of a `__Host-` cookie that does not repeat them).
- **Why:** the `__Host-` prefix makes browsers refuse the cookie unless it is Secure, path `/` and host-only, so no subdomain can set or overwrite it. `HttpOnly` keeps the session token away from scripts, ours or injected. `Lax` (not `Strict`) on the session cookie keeps future single sign-on redirects working; `Strict` on the CSRF cookie costs nothing because only our own scripts read it.
- **Where:** [`app/api/session.py`](../../services/api/app/api/session.py).
- **Proven by:** `tests/test_auth_api.py::TestSessionCookie`, `TestLogout::test_logout_clears_both_cookies`.

### D5.5 Accounts cannot be enumerated

- **Decision:** a wrong password and an unknown account get the same `401` and the same body (`"Email or password is incorrect."`), and both perform exactly one password verification: unknown accounts verify against a dummy hash made at start-up. A disabled account answers the same way. Reset requests always return the same `202` body, whether or not the account exists, and send mail only to a real, enabled account. Throttling applies identically to unknown emails. Audit rows for unknown accounts record `unknown`, never the typed email.
- **Why:** an attacker must not learn which emails have accounts, from responses or from timing.
- **Proven by:** `TestLogin::test_unknown_and_known_accounts_are_indistinguishable`, `test_an_unknown_account_still_costs_a_password_verification`, `TestPasswordReset::test_requesting_a_reset_looks_the_same_for_any_email`, `TestThrottling::test_unknown_accounts_are_throttled_identically`. The second test caught a regression during the concurrency refactor, where Python's `or` short-circuited past the verification for unknown accounts; the code now verifies unconditionally *before* the other checks, with a comment saying why.

### D5.6 Throttling, not lockout, counted before the slow check

- **Decision:** sign-in attempts are limited to 10 per account (`login:account:<email>`) and 100 per client IP (`login:client:<ip>`) per 15 minutes; reset requests to 5 per email per 15 minutes. Each attempt is counted atomically **before** the password is verified; a successful sign-in clears the account's count (not the client's). While throttled, even the correct password gets `429` (`"Too many attempts. Try again later."`) with `Retry-After` in seconds. Refused attempts are not counted, so a flood cannot extend the block forever: the window drains on schedule.
- **Why:** lockout lets an attacker lock real users out on purpose; throttling slows guessing without that. Counting before verifying closes the race where 30 parallel guesses all pass a check-then-count gap. Refusing the correct password while throttled stops the throttle itself from confirming a guess.
- **Costs:** a real user who mistypes ten times waits up to 15 minutes.
- **Client address:** the per-client key is the connecting address. `ClientAddressMiddleware` believes `X-Forwarded-For` only from an address in `HUMANI_T_TRUSTED_PROXIES` (empty by default; Caddy's fixed `172.30.231.2` in compose), and then takes the nearest address that is not a trusted proxy, reading from the right, because entries further left were written by the client. Uvicorn must run with `--no-proxy-headers`: otherwise it rewrites the address from `X-Forwarded-For` for its own trust list (`127.0.0.1` by default) before the app sees the request.
- **Proven by:** `TestThrottling`, `TestClientAddress`, `tests/test_concurrency_and_resources.py::TestThrottleUnderConcurrency`, `tests/test_ratelimit_contract.py`.

### D5.7 Session lifetimes and recent sign-in

- **Decision:** 1 hour idle, 24 hours absolute (`SessionPolicy`). A new token pair at every sign-in, registration and password change, so a token known before sign-in is useless after it (no session fixation). Sensitive actions — today, changing the password — require a sign-in within 15 minutes (`authenticated_at`); otherwise they answer `401` with `"reauthentication_required": true`, and `POST /auth/reauthenticate` with the password moves the session to a new token pair (step-up rotation, [`architecture.md` §5.2](../architecture.md#52-sessions)): the old token stops working at once, `authenticated_at` becomes now, and the assurance level and creation time carry over, so a password alone never extends the 24-hour limit. A wrong password there is `403` and an `auth.reauthentication_failed` audit event. Password change ends every other session and continues this one under a fresh token; password reset ends every session.
- **Why:** NIST 800-63B rev 4 at assurance level 2 (AAL2).
- **Proven by:** `tests/test_sessions.py::TestSessionPolicy`, `TestSessionLifetime`, `tests/test_auth_api.py::TestCurrentSession`, `TestSessionCookie::test_signing_in_always_issues_a_new_token`, `TestChangePassword`, `TestReauthentication`, `tests/test_mfa_api.py::TestTotpEnrolment::test_the_rotated_session_keeps_its_second_factor_level`.

### D5.8 Rate limiter design

- **Decision:** a sliding-window log: each key is a Valkey sorted set of event timestamps (`ratelimit:<key>`). One Lua script removes entries older than the window, refuses if the count is at the limit (returning when the oldest entry leaves the window, for `Retry-After`), and otherwise adds the event and sets the key to expire after the window — atomically, so two API workers cannot both admit the last allowed attempt. In development and tests, `MemoryRateLimiter` has the same behaviour; `check` never creates an entry, and every 1000 operations it sweeps keys whose newest event has left every window, so its memory stays bounded.
- **Why:** sliding windows avoid the burst-at-the-boundary problem of fixed windows. Lua scripts run atomically inside Valkey.
- **Rejected:** fixed windows (bursts); in-process limits in production (each worker would count separately — Settings refuses production without Valkey); slowapi (decorator-based, hard to key by account, and cannot count before the verification).
- **Proven by:** `tests/test_ratelimit_contract.py::TestRateLimiterContract` (memory and real Valkey, including 50 concurrent hits never exceeding a limit of 10), `TestMemoryLimiterIsBounded`, `tests/test_sessions.py::TestMemoryRateLimiter`.

### D5.9 The life of a sign-in request

Following `POST /api/v1/auth/login` through the system shows how the pieces fit. Middleware runs outermost first ([D2.9](02-configuration-and-persistence.md#d29-middleware-as-plain-asgi-in-a-deliberate-order)):

1. **`RequestIdMiddleware`** gives the request an id (or keeps a safe one from the proxy), binds it to every log line, and echoes it in `x-request-id`.
2. **`SecurityHeadersMiddleware`** wraps the response so that whatever happens below, it leaves with CSP, HSTS, `nosniff`, `no-store` and the rest.
3. **`BodySizeLimitMiddleware`** refuses bodies over 1 MiB with `413` before reading them.
4. **`CrossSiteRequestMiddleware`** refuses the write with `403` if `Origin` or `Sec-Fetch-Site` show it came from another site, and with `415` if the body is not JSON.
5. **FastAPI routing and Pydantic** validate the body into `LoginIn`: a valid email address and a password of 1 to 4096 characters, nothing else. Failures become `422` problem documents that never echo the input.
6. **The route** (`routes/auth.py`) calls `AccountService.login` and, on success, sets the two cookies.
7. **`AccountService.login`** (`application/accounts.py`):
   1. normalises the email, then counts the attempt atomically for the client IP and for the account, *before* any slow work, and refuses with `ThrottledError` (`429`) over either limit;
   2. opens a **short transaction** to read the user, then closes it;
   3. verifies the password with Argon2 **in a worker thread, with no database connection held** — against the dummy hash if the account does not exist, so both paths cost the same;
   4. on failure (unknown, disabled or wrong password), writes an `auth.login_failed` audit event in its own short transaction and raises `InvalidCredentialsError` (`401`);
   5. on success, upgrades the hash if its parameters are outdated (still outside any transaction), then opens a second short transaction, re-reads the user to make sure nothing changed meanwhile (not disabled, same password hash), saves the upgraded hash with a version check, creates the session, writes the `auth.login` audit event **last**, and commits;
   6. resets the account's attempt counter.
8. **Error handlers** (`api/errors.py`) turn any domain error into the right status and an RFC 9457 problem document.

### D5.10 Invite-only registration and an operator-created first admin

- **Decision:** there is no public sign-up. An administrator invites by email (`POST /admin/invitations`); the response never contains the token. The emailed link (7 days, single use) is the proof of email ownership; `POST /auth/register` with its token, a password and a display name creates the account and signs it in. Inviting an email that already has an account is `409`. The first administrator is created with `python -m app.cli create-admin --email … --password-stdin`, reading the password from standard input; its display name is the email's local part. The CLI validates emails with the same validator and settings as the API's `EmailStr`, so an address the CLI accepts is one the admin can sign in with.
- **Why:** a humanitarian deployment should not accept strangers. A public "create first admin" endpoint would be a race: whoever finds a fresh install first becomes admin. Passwords are never accepted as arguments, because arguments end up in shell history and in process listings other users of the machine can read; `allow_abbrev=False` stops `--password` from silently matching `--password-stdin`.
- **Proven by:** `TestInvitations`, `tests/test_cli.py::TestCreateAdmin`.

### D5.11 Changing and resetting a password

- **Decision — change** (`POST /account/password`), for a signed-in write:
  1. The same middleware chain runs.
  2. The **`get_current` dependency** (`api/session.py`) reads the `__Host-session` cookie, resolves it in a short transaction (`authenticate`), extends the idle timer, and returns the caller. No cookie or a dead session gives `401`.
  3. Because the method changes state, `get_current` also checks that the `X-CSRF-Token` header matches the session's CSRF token (constant-time comparison of hashes). A mismatch gives `403`.
  4. The service checks the sign-in is recent (15 minutes), verifies the current password (`403` if wrong) and hashes the new one **outside any transaction**, then in one short transaction re-checks the stored hash, saves the user with a version check, revokes all sessions, starts a fresh one and writes the audit event last. The notification email goes out **after** the commit. The response is `204` with new cookies.
- **Decision — reset:** `POST /auth/password-reset` always answers `202` with the same body. For a real, enabled account it cancels any unused links, stores the hash of a new 30-minute token, records `auth.password_reset_requested`, commits, then emails `<origin>/reset-password#token=…`. `POST /auth/password-reset/confirm` checks the link and the policy outside a transaction, then *claims* the link with one conditional update (of two simultaneous uses, exactly one wins), sets the password, revokes every session, records `auth.password_reset_completed`, commits, and emails a "password was changed" notice. A bad, used, expired or superseded link is `400` (`"This link is invalid or has expired."`), deliberately not saying which.
- **Why:** NIST: the user is told about credential changes, and every other session ends so a thief's session does not survive a password change. A newer link cancelling older ones means only the most recent email works.
- **Proven by:** `TestChangePassword`, `TestPasswordReset`, `tests/test_concurrency_and_resources.py::TestSingleUseTokens`.

### D5.12 Cross-site request forgery: three independent layers

- **Decision:**
  1. state-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`) whose `Sec-Fetch-Site` is present and not `same-origin`, or whose `Origin` is present and not exactly the public origin, are refused with `403` (`CrossSiteRequestMiddleware`) — even at sign-in;
  2. every signed-in write must send `X-CSRF-Token` matching the session's CSRF token (`get_current`), or gets `403` (`"Missing or invalid CSRF token."`);
  3. request bodies must be `application/json`, or get `415`.
- **Why:** any one layer stops a forged request on its own. An HTML form on another site cannot send JSON or custom headers, cannot read the CSRF cookie, and a cross-site JSON request needs a CORS preflight this API never grants. The SPA and the API share one origin behind Caddy, so there is no CORS configuration at all.
- **Non-browser clients:** a request with neither `Origin` nor `Sec-Fetch-Site` (curl, Postman) passes layer 1, because forgery needs a victim's browser. Layer 2 still applies after sign-in. Reads (`GET`, `HEAD`, `OPTIONS`) are never blocked.
- **Proven by:** `tests/test_csrf.py` (`TestOriginCheck`, `TestSessionToken`, `TestBodyFormat`).

### D5.13 Email

- **Decision:** SMTP through `aiosmtplib` (STARTTLS by default, 10-second timeout); Mailpit catches all mail in development. Links carry their token after `#` (`/reset-password#token=…`, `/accept-invitation#token=…`). Emails are sent after the commit. Reset and password-change notices are sent "quietly" (a mail failure is logged as `email_delivery_failed`, not shown); invitations are sent "loudly" (a failure returns `502`, `"The email could not be sent. The action was saved; try sending again."`, so the admin knows). Without `SMTP_HOST`, messages are kept in memory (`RecordingEmailProvider`) — development and tests only; Settings refuses it in production.
- **Why:** browsers never send the part after `#` to a server, so tokens cannot leak into server or proxy logs or `Referer` headers. A quiet reset email keeps a mail outage from revealing whether an account exists, and must not undo a password change that already happened.
- **Rejected:** an HTTP mail API (SendGrid, SES) by default: a vendor dependency and data leaving the deployment, which the one rule discourages.
- **Proven by:** `TestPasswordReset::test_the_link_carries_the_token_in_the_fragment_so_it_never_reaches_logs`, `TestChangePassword::test_the_user_is_told_by_email`, `TestPasswordReset::test_the_user_is_told_their_password_changed`, `tests/test_concurrency_and_resources.py::TestNoConnectionIsHeldDuringSlowWork::test_email_is_sent_only_after_the_transaction_has_ended`.

### D5.14 Rules for concurrency and resources

Written in [`architecture.md` §5.7](../architecture.md#57-concurrency-and-resources) and enforced by `tests/test_concurrency_and_resources.py` and the contract suites:

1. Never hold a database connection across slow work (hashing, email, outbound HTTP).
2. Side effects (email) happen after commit.
3. The audit event is the last write in a transaction, keeping the chain lock brief and the lock order consistent (no deadlocks).
4. No blind overwrites of shared records: writes are conditional ("only if still unused / unrevoked": `claim`, `revoke`, `touch`, `mark_reauthenticated`) or version-checked (`users.version`, optimistic locking). A lost update raises `ConcurrentUpdateError` (`409`, `"The record changed while you were editing it. Reload and try again."`).
5. Single-use tokens are spent with one conditional `UPDATE`; of two simultaneous uses, exactly one wins.
6. Count rate-limited attempts before the slow check.
7. Bounded pools that fail fast with `503`; server-side statement and idle-transaction timeouts ([D2.7](02-configuration-and-persistence.md#d27-a-bounded-pool-that-fails-fast-and-server-side-timeouts)).
8. Bounded memory: expiring in-process structures, the body limit, `purge-expired`.
9. Everything opened is closed: `Providers.close()`.

**Why:** every one of these was a real defect found in review: a sign-in burst could exhaust the pool; a late "last seen" update could un-revoke a session; a racing login could re-enable a disabled account; a single-use link could be used twice; a key rotation could revive a shredded project.

The test harness proves rule 1 without a database: `TrackingStorage` counts open transactions, and `ObservingHasher` and `ObservingEmail` record that count every time they hash or send — the tests assert it was always `0`.

### D5.15 Administration and visibility

- **Decision:** admin-only use cases call `_require_admin` (`403`, `"Administrators only."`, for a signed-in non-admin; `401` before that for anyone not signed in). Disabling an account (`POST /admin/users/{id}/disable`) saves it with a version check, revokes all its sessions, and records `account.disabled` in one transaction; an unknown id is `404`. An admin cannot disable their own account (`409`), nor the last enabled administrator (`409` with `"last_admin": true`). The transaction first locks every enabled administrator's row (`lock_enabled_admins`, `SELECT … FOR UPDATE` in id order), so two admins disabling each other at once are serialised and the second is refused. Re-enabling (`POST /admin/users/{id}/enable`) needs a sign-in within 15 minutes, records `account.enabled`, and revives none of the sessions that disabling ended. A session that belongs to someone else is `404` (`"No such session."`), not `403`: its existence is not the caller's business. `GET /account/sessions` lists the caller's active sessions with `id`, `created_at`, `last_seen_at`, `user_agent` and `current` — never a token.
- **Why:** deny by default, and do not confirm the existence of other people's objects (the same rule projects will follow: non-members get `404`).
- **Proven by:** `TestInvitations::test_only_admins_can_invite`, `TestSessionManagement::test_another_users_session_cannot_be_revoked_or_even_seen`, `TestLogin::test_a_disabled_account_cannot_sign_in`, `TestAccountAdministration`, `tests/test_storage_contract.py::TestLastAdminUnderConcurrency` (memory and Postgres), `TestPostgresAdminLock`.

### D5.16 Purging what has ended

- **Decision:** `python -m app.cli purge-expired` deletes sessions that are revoked, idle for over an hour or older than 24 hours; reset links that are used or expired; and invitations that are accepted or expired. It prints the three counts. The worker (M4) will run it on a schedule.
- **Why:** these tables would otherwise grow forever; a session row is useless once it can never be active again.
- **Proven by:** `tests/test_concurrency_and_resources.py::TestBoundedGrowth`.

### Deliberately not used

| Not used | Why not |
| --- | --- |
| Redis 8 | Its licence is no longer open source in the usual sense; Valkey is the drop-in, BSD-licensed fork |
| JWT libraries | Server-side sessions instead (D5.3) |
| passlib, bcrypt | passlib is unmaintained; bcrypt is not memory-hard (D5.2) |
| slowapi | Hard to key limits by account; we needed attempt counting before verification (D5.8) |
| SMS for anything | Not a safe second factor (NIST); MFA in phase 06 uses passkeys and TOTP |

## Libraries

### argon2-cffi 25.1.0

- **What it does:** Argon2 password hashing via the reference C implementation; produces self-describing hash strings (`$argon2id$v=19$m=65536,t=3,p=4$…`) that record their parameters, and reports when a hash needs upgrading (`check_needs_rehash`).
- **Why this one:** the reference binding, recommended by OWASP; the RFC 9106 profiles ship with it.
- **Alternatives:** passlib (wraps many algorithms, but is unmaintained); bcrypt (D5.2).
- **Trade-offs:** CPU and memory cost is deliberate; it must run off the event loop (it does).

### redis-py 8.1.0 (`redis`)

- **What it does:** client for Redis-protocol servers. We use its asyncio client against **Valkey** for the shared rate limiter, including `register_script` for the Lua script.
- **Why this one:** the standard, maintained client, with asyncio support and script helpers; Valkey speaks the same protocol.
- **Trade-offs:** the server is Valkey, not Redis, by licence choice. A Valkey outage now answers `503` with `Retry-After` and refuses the attempt (G1, closed in [06](06-mfa.md)).

### Valkey 8 (`valkey/valkey:8-alpine`)

- **What it does:** an in-memory key–value store; here, the shared rate-limit counters.
- **Why this one:** the open-source (BSD) continuation of Redis after Redis changed its licence. It speaks the same protocol, so redis-py works unchanged.
- **Configuration:** password from a Docker secret; persistence off (`--save '' --appendonly no`), because rate-limit counts are disposable.
- **Trade-offs:** another service to run.

### aiosmtplib 5.1.3

- **What it does:** sends email over SMTP from async code, with STARTTLS and authentication.
- **Why this one:** the standard library's `smtplib` blocks the event loop; this does not.
- **Alternatives:** an HTTP mail API (D5.13).
- **Trade-offs:** one connection per message; fine at our volume. The TLS path is not yet tested (G6; `email/smtp.py` is at about 59% coverage).

### email-validator 2.3.0

- **What it does:** checks email address syntax, refuses special-use and reserved domains (`.local`, `.test`, `.localhost`…), and returns a normalised form. Pydantic's `EmailStr` uses it; the CLI calls it directly with the same settings.
- **Why this one:** email syntax is genuinely hard; a regex gets it wrong.
- **Trade-offs:** it can optionally check DNS (deliverability); that is turned off, because it would mean a network call per validation. Its messages can name offending characters ([02 known gaps](02-configuration-and-persistence.md#known-gaps)).

## Files to read, in order

From the rules, to the use cases, to HTTP, to the tests that pin them down:

1. [`app/domain/passwords.py`](../../services/api/app/domain/passwords.py) — the policy, the blocklist, `_repeating_unit`, `_uses_personal_details`.
2. [`app/domain/sessions.py`](../../services/api/app/domain/sessions.py) — tokens, `SessionPolicy`, `is_active`, `is_recently_authenticated`.
3. [`app/domain/accounts.py`](../../services/api/app/domain/accounts.py) — `normalize_email`, `User.version`, `Invitation` and `PasswordReset.is_usable`, the errors and why their messages are safe.
4. [`app/auth/base.py`](../../services/api/app/auth/base.py), [`argon2.py`](../../services/api/app/auth/argon2.py), [`fake.py`](../../services/api/app/auth/fake.py) — synchronous on purpose; the fake that counts verifications.
5. [`app/ratelimit/base.py`](../../services/api/app/ratelimit/base.py), [`memory.py`](../../services/api/app/ratelimit/memory.py), [`valkey.py`](../../services/api/app/ratelimit/valkey.py) — "refused events are not counted"; the Lua script.
6. [`app/email/base.py`](../../services/api/app/email/base.py), [`smtp.py`](../../services/api/app/email/smtp.py), [`recording.py`](../../services/api/app/email/recording.py).
7. [`app/application/accounts.py`](../../services/api/app/application/accounts.py) — the module docstring's four-step shape first, then `login`, `authenticate`, `change_password`, `request_password_reset`, `confirm_password_reset`, `invite`, `register`, `_send_quietly`.
8. [`app/storage/postgres/storage.py`](../../services/api/app/storage/postgres/storage.py) — `PostgresUserRepository.save` (version check), `PostgresSessionRepository.touch` / `revoke` (conditional), `claim` on invitations and resets.
9. [`app/api/session.py`](../../services/api/app/api/session.py) — the two cookies and `get_current`.
10. [`app/api/routes/auth.py`](../../services/api/app/api/routes/auth.py), [`account.py`](../../services/api/app/api/routes/account.py), [`admin.py`](../../services/api/app/api/routes/admin.py), [`app/api/schemas/accounts.py`](../../services/api/app/api/schemas/accounts.py).
11. [`app/api/middleware.py`](../../services/api/app/api/middleware.py), `CrossSiteRequestMiddleware` — layers 1 and 3.
12. [`app/cli.py`](../../services/api/app/cli.py) — `_create_admin`, `_purge`.
13. [`tests/support.py`](../../services/api/tests/support.py) — `FakeClock`, `TrackingStorage`, `ObservingHasher`, `ObservingEmail`, `make_harness`; then [`test_auth_api.py`](../../services/api/tests/test_auth_api.py), [`test_csrf.py`](../../services/api/tests/test_csrf.py), [`test_concurrency_and_resources.py`](../../services/api/tests/test_concurrency_and_resources.py).

## Set up

[00 — Setup](00-setup.md) through S0.12, with the API running, the admin created, and the Postman collection set up — including the `login (device B)` copy from [S0.10](00-setup.md#s010-postman). Also add the cookie-capture script to the Post-response tab of `POST {{base}}/account/password` and of `POST {{base}}/auth/reauthenticate`: a password change and a step-up both issue new cookies and revoke the old session.

T5.10.6 relies on the API running with `--no-proxy-headers`, as the development command in [S0.9](00-setup.md#s09-start-the-api) does (from `services\api`): `uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers`.

Run the groups in order the first time: later groups rely on accounts and passwords created by earlier ones. The admin password changes twice along the way:

| From | Admin password |
| --- | --- |
| the start | `violet-harbour-quietly-41-lanterns` |
| T5.8.3 | `quiet-lanterns-over-the-harbour-41` |
| T5.9.5 | `lanterns-and-harbours-at-dawn-41` <!-- gitleaks:allow --> |

## Manual tests

### T5.1 Signing in

| # | Request (`POST {{base}}/auth/login`) | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T5.1.1 | `{"email":"admin@example.org","password":"violet-harbour-quietly-41-lanterns"}` | `200`, body `{"user":{"id":…,"email":"admin@example.org","display_name":"admin","is_admin":true}}`, two `set-cookie` headers; variables `session` and `csrf` filled | Happy path | No variables: the capture script is missing |
| T5.1.2 | Same, email in capitals `ADMIN@EXAMPLE.ORG` | `200` | Emails are case-insensitive (`normalize_email`) | `401`: normalisation lost |
| T5.1.3 | Correct email, wrong password | `401`, `"detail":"Email or password is incorrect."`, no `set-cookie` | | |
| T5.1.4 | `nobody@example.org`, any password | `401`, **byte-for-byte the same body as T5.1.3** | No account enumeration (D5.5) | Any difference — wording, status, a header — tells an attacker which accounts exist |
| T5.1.5 | `{"email":"x"}` | `422`; `errors` names `email` (not a valid address) and `password` (`Field required`); the value `x` is not echoed | Shape validation at the boundary | |
| T5.1.6 | `{"email":"not-an-email","password":"x"}` | `422` | | |

After T5.1.3 and T5.1.4, sign in successfully (T5.1.1) before [T5.10](#t510-throttling), because failures count towards the throttle.

### T5.2 Cookies and stored hashes

- **Do:** inspect the `set-cookie` headers from T5.1.1; then in pgAdmin:

```sql
SELECT id, encode(token_hash, 'hex') AS token_hash, created_at, last_seen_at, authenticated_at, revoked_at, user_agent
FROM sessions ORDER BY created_at DESC LIMIT 5;
```

- **Expect:** `__Host-session=…; HttpOnly; Path=/; SameSite=lax; Secure` and `__Host-csrf=…; Path=/; SameSite=strict; Secure` — no `Domain`, no `Max-Age`, and no `HttpOnly` on the CSRF cookie (the web app must read it). In the database, `token_hash` is 64 hex characters that do **not** match your cookie value (a 43-character URL-safe string); `user_agent` is Postman's.
- **Proves:** D5.3 and D5.4: only a one-way hash is stored; the cookies are host-bound.
- **If it fails:** a stored value equal to the cookie means tokens are stored in the clear — critical.

### T5.3 The current session, logout and replay

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.3.1 | `GET {{base}}/auth/session` | `200`, your `user` and `session` (with `current: true`) | The cookie resolves to a session |
| T5.3.2 | Copy the current `{{session}}` value somewhere | | |
| T5.3.3 | `POST {{base}}/auth/logout` (with the `X-CSRF-Token` header) | `204`; two `set-cookie` headers with `Max-Age=0` | Both cookies are cleared, with matching attributes |
| T5.3.4 | `GET {{base}}/auth/session` with the old cookie value from T5.3.2 | `401`, `"Sign in to continue."` — the session is dead **on the server**, not just in the client | Logout revokes (D5.3) |

- **If it fails:** a `200` in T5.3.4 means logout only cleared cookies; anyone holding a copy of the token would still be signed in.

Sign in again (T5.1.1) before continuing.

### T5.4 Cross-site request forgery

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.4.1 | `POST {{base}}/auth/logout` with the `X-CSRF-Token` header **disabled** | `403`, `"Missing or invalid CSRF token."`; then `GET /auth/session` is still `200` | Layer 2 |
| T5.4.2 | Same, with `X-CSRF-Token: guess` | `403` | Layer 2 |
| T5.4.3 | `POST {{base}}/auth/login` with header `Origin: https://evil.example` | `403`, `"Cross-site requests are not allowed."`, even with correct credentials | Layer 1 |
| T5.4.4 | `POST {{base}}/auth/login` with header `Sec-Fetch-Site: cross-site` | `403` | Layer 1 |
| T5.4.5 | `POST {{base}}/auth/login` with body type *x-www-form-urlencoded* | `415`, `"Request bodies must be application/json."` | Layer 3 |
| T5.4.6 | `POST {{base}}/auth/login` with body type *raw → Text* containing JSON | `415` | Layer 3 |
| T5.4.7 | In the browser, at http://127.0.0.1:8731/docs, sign in through *Try it out* | `200` | A same-origin browser request passes |

- **If it fails:** a `200` or `204` in T5.4.1 to T5.4.6 means a layer is gone; each layer must hold on its own (D5.12). A `403` in T5.4.7 usually means the address bar says `localhost` instead of `127.0.0.1`.

### T5.5 Sessions on several devices

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.5.1 | Sign in as admin with `login` (device A) and with `login (device B)` | two sessions; `{{session}}` and `{{session_b}}` differ | A new token at every sign-in |
| T5.5.2 | `GET {{base}}/account/sessions` | two entries, exactly one `current: true`, each with `id`, `created_at`, `last_seen_at`, `user_agent` — no token fields | Users can see where they are signed in |
| T5.5.3 | As device B: `DELETE {{base}}/account/sessions/<device A's id>` | `204`; A's `GET /auth/session` → `401`; B's → `200` | Signing out one device |
| T5.5.4 | Sign in three times with `login`, then from the last: `POST {{base}}/account/sessions/revoke-others` | `204`; only the last session still works (device B's → `401`) | Sign out everywhere else, in one bounded operation |

- **If it fails:** a revoked session that still answers `200` is a serious defect — check `revoke` is conditional and `authenticate` checks `revoked_at`.

### T5.6 Invitations and registration

Signed in as the admin:

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.6.1 | `POST {{base}}/admin/invitations` `{"email":"kofi@example.org"}` | `201` with `id`, `email`, `expires_at` (7 days ahead); **no token** in the body | The link goes only to the inbox |
| T5.6.2 | Open Mailpit | "You have been invited to humani-t", `admin invited you to humani-t.`, link `http://127.0.0.1:8731/accept-invitation#token=…`, "The link works once and expires in 7 days." | Token after `#` (D5.13) |
| T5.6.3 | Repeat T5.6.1 with `admin@example.org` | `409`, `"An account with this email already exists."` | Existing account |
| T5.6.4 | `POST {{base}}/auth/register` `{"token":"<token from email>","password":"short","display_name":"Kofi"}` (it needs no session; any cookie sent with it is ignored) | `422`, `violations: ["too_short"]` | Policy |
| T5.6.5 | Same, with password `kofi.mensah.2026!` and display name `Kofi Mensah` | `422`, `violations: ["contains_personal_details"]` | Personal-detail screening |
| T5.6.6 | Same, with password `the-river-bends-twice-near-tamale` | `201`, `user.email` = `kofi@example.org`, `user.display_name` = `Kofi Mensah`, `set-cookie` present; the variables now hold **Kofi's** session. **Note `user.id`** | Invitation = verified email; registration signs in |
| T5.6.7 | Send T5.6.6 again | `400`, `"This invitation is invalid or has expired."` | Single use |
| T5.6.8 | As Kofi (current variables, `Cookie` header enabled): `POST {{base}}/admin/invitations` `{"email":"ama@example.org"}` | `403`, `"Administrators only."` | Admins only |
| T5.6.9 | Without any cookies (untick the `Cookie` header on this request): `POST {{base}}/admin/invitations` | `401`, `"Sign in to continue."` | Anonymous callers are refused before anything else |
| T5.6.10 | Sign in as Kofi with `login (device B)` (his session now lives in `session_b`), then as the admin with `login` | both `200` | Sets up T5.6.11 and T5.7 |
| T5.6.11 | pgAdmin: `SELECT s.id FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = 'kofi@example.org' AND s.revoked_at IS NULL;` — then as admin: `DELETE {{base}}/account/sessions/<one of Kofi's ids>` | `404`, `"No such session."` (not `403`); Kofi's device B still `200` on `GET /auth/session` | Another user's session is invisible (D5.15) |

- **If it fails:** a token in T5.6.1's body defeats the email-ownership proof; a `201` in T5.6.7 means the claim is not single-use.

### T5.7 Disabling an account

| # | Do | Expect |
| --- | --- | --- |
| T5.7.1 | As admin: `POST {{base}}/admin/users/<Kofi's id>/disable` | `204` |
| T5.7.2 | As device B (Kofi): `GET {{base}}/auth/session` | `401` — his sessions ended |
| T5.7.3 | Sign in as Kofi | `401`, the same body as a wrong password |
| T5.7.4 | pgAdmin: `SELECT email, is_disabled, version FROM users;` | Kofi `is_disabled = true`, `version` increased (from `0` to `1`) |
| T5.7.5 | As admin: `POST {{base}}/admin/users/00000000-0000-0000-0000-000000000000/disable` | `404`, `"No such user."` |
| T5.7.6 | `POST {{base}}/auth/password-reset` `{"email":"kofi@example.org"}` | `202`, the usual body — and **no** email in Mailpit |
| T5.7.7 | As admin: `POST {{base}}/admin/users/<the admin's own id>/disable` (the id is `user.id` in `GET {{base}}/auth/session`) | `409`, `"You cannot disable your own account."`; `GET /auth/session` still `200` |
| T5.7.8 | pgAdmin, as `postgres` on `humani_t`, in one query window: `BEGIN; SELECT email FROM users WHERE is_admin AND NOT is_disabled FOR UPDATE;` and leave the transaction open. In Postman, as admin: `POST {{base}}/admin/users/00000000-0000-0000-0000-000000000000/disable`. Then run `ROLLBACK;` in pgAdmin | Postman keeps waiting until the `ROLLBACK`, then answers `404`, `"No such user."` |
| T5.7.9 | As admin, within 15 minutes of signing in: `POST {{base}}/admin/users/<Kofi's id>/enable` | `204`; signing in as Kofi gives `200`; device B's old session is still `401` on `GET /auth/session` |
| T5.7.10 | Signed in as Kofi: `POST {{base}}/admin/users/<Kofi's id>/enable` | `403`, `"Administrators only."` |
| T5.7.11 | pgAdmin: `SELECT action, target_id FROM audit_events ORDER BY sequence DESC LIMIT 6;` | `account.enabled` and, further down, `account.disabled`, both with Kofi's id |

The last-admin refusal (`409`, `"last_admin": true`) cannot be reached one request at a time: whoever sends the request is an enabled admin, so disabling someone else always leaves one. It needs two admins disabling each other at the same moment; T5.7.8 shows that such requests queue on the admin lock, and `TestLastAdminUnderConcurrency` races them on Postgres. Re-enabling more than 15 minutes after signing in answers `401` with `"reauthentication_required": true` until `POST /auth/reauthenticate`.

- **Proves:** D5.15 and D5.5: disabling ends sessions at once, a disabled account looks like any failed sign-in, and gets no reset link; no admin can lock themselves out; disabling waits on the admin lock; re-enabling is admin-only, audited, and gives back no old session.
- **If it fails:** a `200` in T5.7.2 means disabling does not reach live sessions; an immediate answer in T5.7.8 means disabling no longer takes the lock; `200` for device B in T5.7.9 means re-enabling revived a session.

### T5.8 Changing the password

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.8.1 | Signed in as admin within the last 15 minutes: `POST {{base}}/account/password` `{"current_password":"violet-harbour-quietly-41-lanterns","new_password":"password"}` | `422`, `violations: ["too_short","too_common"]` | Policy, with every reason reported |
| T5.8.2 | Same with a wrong `current_password` | `403`, `"The current password is incorrect."` | |
| T5.8.3 | With the correct current password and `new_password` = `quiet-lanterns-over-the-harbour-41` | `204` with new `set-cookie` headers (captured by the script); Mailpit: "Your humani-t password was changed"; every other session (including any device B session) `401` | D5.7, D5.11 |
| T5.8.4 | Sign in with the old password | `401` | |
| T5.8.5 | Sign in with the new password | `200` | |
| T5.8.6 | `POST {{base}}/auth/reauthenticate` `{"password":"wrong-password-entirely"}` | `403`, `"The password is incorrect."`; an `auth.reauthentication_failed` row in `audit_events` | Step-up refuses a wrong password, on record |
| T5.8.8 | Copy the `session` collection variable. `POST {{base}}/auth/reauthenticate` `{"password":"<current>"}`, then send `GET {{base}}/auth/session` with the header `Cookie: __Host-session=<the copied value>` | `204` with new `set-cookie` headers for `__Host-session` and `__Host-csrf` (the variables change); the copied token gets `401`; the normal request gets `200` | Step-up rotates the token (D5.7) |
| T5.8.7 | Optional, slow: stay signed in, wait 16 minutes (a `GET /auth/session` every few minutes keeps the session alive, though the idle limit is an hour), then try T5.8.3's request again with the current passwords | `401` with `"reauthentication_required": true` and `"Confirm your password to continue."`; then `POST /auth/reauthenticate` `{"password":"<current>"}` → `204`, and the change works | The 15-minute window |

From here on, the admin password is the one you set in T5.8.3.

### T5.9 Password reset

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.9.1 | `POST {{base}}/auth/password-reset` `{"email":"admin@example.org"}` | `202`, `{"status":"If an account exists for that email, a reset link has been sent."}` | |
| T5.9.2 | Same for `nobody@example.org` | **identical** `202` body; no email in Mailpit | No enumeration |
| T5.9.3 | Mailpit | one "Reset your humani-t password", link `http://127.0.0.1:8731/reset-password#token=…`, "The link works once and expires in 30 minutes." | Token after `#` |
| T5.9.4 | Request a second reset for the admin, then try the **first** link's token in T5.9.5's request | `400`, `"This link is invalid or has expired."` | Newest link wins |
| T5.9.5 | `POST {{base}}/auth/password-reset/confirm` `{"token":"<newest token>","new_password":"lanterns-and-harbours-at-dawn-41"}` | `204`; every session `401`; a "password was changed" email | Fictional example password <!-- gitleaks:allow --> |
| T5.9.6 | Send T5.9.5 again | `400` | Single use |
| T5.9.7 | Optional, slow: request a reset, wait 31 minutes, confirm | `400` | 30-minute expiry |
| T5.9.8 | Sign in as admin with the password from T5.9.5 | `200` | The reset really changed the password |
| T5.9.9 | From `services\api`, `docker compose -f ..\..\compose.dev.yaml pause mailpit`. Send `POST {{base}}/auth/password-reset` for the admin, then for `nobody@example.org`, and compare Postman's response times. Then `docker compose -f ..\..\compose.dev.yaml unpause mailpit` | both `202`, both about as fast, though the mail server is not answering; after unpausing, the admin's reset email arrives, or the API window shows an `email_delivery_failed` warning if the send timed out | The email is sent off the request path, so timing does not reveal which emails have accounts |

- **If it fails:** a different body in T5.9.2 reveals which emails have accounts; a `204` in T5.9.4 or T5.9.6 means links are not single-use.

### T5.10 Throttling

Start right after a successful admin sign-in (it resets the account's count).

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.10.1 | Postman Collection Runner: run a login request for the admin with a **wrong** password for 12 iterations | iterations 1–10 → `401`; 11 and 12 → `429` with a `Retry-After` header (seconds, at most 900) | D5.6 |
| T5.10.2 | Immediately sign in with the **correct** password | `429` | The throttle must not confirm a correct guess |
| T5.10.3 | Wait the `Retry-After` seconds (up to 15 minutes), sign in correctly | `200` | Throttling, not lockout |
| T5.10.4 | Runner: 6 iterations of `POST {{base}}/auth/password-reset` `{"email":"nobody2@example.org"}` | 1–5 → `202`; 6 → `429` with `Retry-After`; no emails | Per-email limit, applied identically to unknown addresses |
| T5.10.4a | Sign in as admin. Runner: 11 iterations of `POST {{base}}/auth/reauthenticate` `{"password":"wrong-password-entirely"}` with the CSRF header, then once with the correct password | 1–10 → `403`; 11 → `429` with `Retry-After`; the correct password → `429` too | Step-up shares the sign-in limit, so a stolen session cannot guess the password |
| T5.10.4b | After the `Retry-After`, sign in again and do the same with `POST {{base}}/account/password` `{"current_password":"wrong-password-entirely","new_password":"<any long password>"}` | 1–10 → `403`; 11 → `429` | A password change counts the same way |
| T5.10.5 | Optional: more than 15 minutes after T5.9, the same 6 iterations for `admin@example.org` | 1–5 → `202`, 6 → `429`; Mailpit shows 5 new reset emails | The limit counts real accounts the same way |
| T5.10.6 | With the API started with `--no-proxy-headers` (the development command, Set up): `POST {{base}}/auth/login` with a wrong password and the header `X-Forwarded-For: 198.51.100.9`. Then from `services\api`: `docker compose -f ..\..\compose.dev.yaml exec valkey sh -c 'REDISCLI_AUTH=$(cat /run/secrets/valkey_password) valkey-cli --scan --pattern ratelimit:login:client:*'` | `401`; the client key is `ratelimit:login:client:127.0.0.1`, never `…:198.51.100.9` | A client cannot choose the address its throttle counts (D5.6) |

- **If it fails:** an eleventh `401` means attempts are not being counted (or counted after verification); `429` from the first attempt means earlier failures are still in the window — see [S0.14](00-setup.md#s014-troubleshooting). The per-client limit (100 per 15 minutes from `127.0.0.1`) can also be reached after a long session of tests.

### T5.11 Mail and Valkey failures

Run from `services\api`.

| # | Do | Expect | Proves |
| --- | --- | --- | --- |
| T5.11.1 | `docker compose -f ..\..\compose.dev.yaml stop mailpit`, then `POST {{base}}/auth/password-reset` for the admin | still `202` (quiet send); an `email_delivery_failed` warning in the API window | No enumeration through mail errors (D5.13) |
| T5.11.2 | With Mailpit still stopped, as admin `POST {{base}}/admin/invitations` for a new email | `502`, `"The email could not be sent. The action was saved; try sending again."`; pgAdmin shows the new row in `invitations` | The admin must know; the invitation exists but its link was never delivered |
| T5.11.3 | `docker compose -f ..\..\compose.dev.yaml start mailpit` | | |
| T5.11.4 | `docker compose -f ..\..\compose.dev.yaml stop valkey`, then `POST {{base}}/auth/login`; also `GET {{base}}/auth/session` | login: `503` with `Retry-After` (G1, closed in [06](06-mfa.md)); before that fix it was `500`. Note that this `500` has **no** `x-request-id` and none of the security headers, and its `unhandled_error` log line has no `request_id` ([D2.11](02-configuration-and-persistence.md#d211-errors-as-problem-documents)). `GET /auth/session` still `200`: only sign-in and reset requests use the limiter | Tracked in Known gaps |
| T5.11.5 | `docker compose -f ..\..\compose.dev.yaml start valkey` | sign-in works again | |

### T5.12 Operator commands

Run from `services\api`. PowerShell pipes the string into the command's standard input, which is how the password is supplied safely.

| # | Run | Expect |
| --- | --- | --- |
| T5.12.1 | `"password" \| uv run --env-file .env python -m app.cli create-admin --email second@example.org --password-stdin` | exit 1, `password refused: too_short, too_common` |
| T5.12.2 | `"violet-harbour-quietly-41-lanterns" \| uv run --env-file .env python -m app.cli create-admin --email admin@humani-t.local --password-stdin` | exit 1, `email refused: The part after the @-sign is a special-use or reserved name that cannot be used with email.` |
| T5.12.3 | `uv run --env-file .env python -m app.cli create-admin --email a@example.org --password x` | exit 2, a usage error: `the following arguments are required: --password-stdin` — passwords are never accepted as arguments |
| T5.12.4 | `"violet-harbour-quietly-41-lanterns" \| uv run --env-file .env python -m app.cli create-admin --email admin@example.org --password-stdin` | exit 1, `an account with this email already exists` |
| T5.12.5 | `uv run --env-file .env python -m app.cli purge-expired` | `purged N session(s), N reset link(s), N invitation(s)` — non-zero after the tests above, which revoked sessions and spent links |

Check an exit code with `$LASTEXITCODE` straight after the command.

### T5.13 What the audit log recorded

- **Do:** run [T4.2](04-audit-log.md#t42-the-audit-trail-of-your-own-actions) and [T4.5](04-audit-log.md#t45-verify-the-development-chain).
- **Expect:** an event for each action above, with the actions listed in [D4.8](04-audit-log.md#d48-what-is-recorded-today); no email addresses; the chain intact.
- **Proves:** brief rule 16 for every account action.

### Results checklist

| Section | Passed | Notes |
| --- | --- | --- |
| T5.1 Signing in | ☐ | |
| T5.2 Cookies and stored hashes | ☐ | |
| T5.3 Session, logout, replay | ☐ | |
| T5.4 CSRF | ☐ | |
| T5.5 Multiple sessions | ☐ | |
| T5.6 Invitations and registration | ☐ | |
| T5.7 Disabling accounts | ☐ | |
| T5.8 Changing the password | ☐ | |
| T5.9 Password reset | ☐ | |
| T5.10 Throttling | ☐ | |
| T5.11 Mail and Valkey failures | ☐ | |
| T5.12 Operator commands | ☐ | |
| T5.13 Audit trail | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Length, no composition rules, Unicode, common/repeated/personal passwords refused, violations reported together | `tests/test_passwords.py::TestPasswordPolicy` |
| Equivalent Unicode forms normalise to the same password | `tests/test_passwords.py::TestNormalisation` |
| Argon2id, salted, verifies only its own password, upgrades weaker hashes, survives malformed hashes | `tests/test_passwords.py::TestArgon2PasswordHasher` |
| 256-bit tokens, never repeated, only hashes kept; NIST AAL2 lifetimes | `tests/test_sessions.py::TestTokens`, `TestSessionPolicy`, `TestSessionLifetime` |
| Sign-in: success, case-insensitive, indistinguishable unknown accounts with equal work, disabled accounts refused, malformed input rejected | `tests/test_auth_api.py::TestLogin` |
| Cookie attributes; tokens never stored in the clear; a new token at every sign-in | `tests/test_auth_api.py::TestSessionCookie` |
| Anonymous and forged tokens 401; idle 1 h, absolute 24 h, activity extends | `tests/test_auth_api.py::TestCurrentSession` |
| Logout ends the session on the server and clears both cookies | `tests/test_auth_api.py::TestLogout` |
| List and revoke sessions; others' sessions invisible; sign out everywhere else | `tests/test_auth_api.py::TestSessionManagement` |
| Password change: other sessions end, current password required, policy, 15-minute window, email | `tests/test_auth_api.py::TestChangePassword` |
| No open registration; admin-only invitations without the token in the response; single use; 7 days; policy | `tests/test_auth_api.py::TestInvitations` |
| Reset: same response for any email, only real accounts mailed, token in the fragment, single use, 30 minutes, newest wins, policy, notice | `tests/test_auth_api.py::TestPasswordReset` |
| Throttling per account, while throttled even the right password, lifts after the window, unknown accounts identical, resets per email | `tests/test_auth_api.py::TestThrottling` |
| The throttle counts the connecting address; `X-Forwarded-For` only from a trusted proxy, read from the right | `tests/test_auth_api.py::TestClientAddress` |
| Step-up issues new cookies, the old token stops working, a wrong password changes nothing, the 24-hour limit is not extended, audited | `tests/test_auth_api.py::TestReauthentication` |
| No disabling your own account; re-enabling works, needs a recent sign-in, is admin-only, audited, and revives no session | `tests/test_auth_api.py::TestAccountAdministration` |
| Admins disabling each other at once always leave one enabled admin (memory and Postgres); the admin lock serialises | `tests/test_storage_contract.py::TestLastAdminUnderConcurrency`, `TestPostgresAdminLock` |
| Sign-ins, failures, sign-outs and credential changes recorded without emails or passwords | `tests/test_auth_api.py::TestAuditTrail` |
| The three CSRF layers, each on its own | `tests/test_csrf.py` |
| No connection held across hashing or email | `tests/test_concurrency_and_resources.py::TestNoConnectionIsHeldDuringSlowWork` |
| 30 parallel guesses cannot exceed the per-account limit | `tests/test_concurrency_and_resources.py::TestThrottleUnderConcurrency` |
| A late `touch` cannot revive a revoked session; a stale user copy cannot overwrite a newer one | `tests/test_concurrency_and_resources.py::TestNoLostUpdates` |
| Two simultaneous uses of one reset link or invitation have one winner | `tests/test_concurrency_and_resources.py::TestSingleUseTokens`, `tests/test_storage_contract.py::TestPostgresConcurrency::test_concurrent_claims_of_one_token_have_exactly_one_winner` |
| Expired sessions and spent tokens purged; live ones kept | `tests/test_concurrency_and_resources.py::TestBoundedGrowth` |
| The rate limiter is exact, slides, does not count refusals, resets, and holds under 50 concurrent hits (memory and Valkey); memory stays bounded | `tests/test_ratelimit_contract.py` |
| The CLI creates an admin from stdin, refuses weak passwords, refuses passwords as arguments, refuses emails the API would refuse | `tests/test_cli.py::TestCreateAdmin` |

```powershell
uv run pytest tests/test_passwords.py tests/test_sessions.py tests/test_auth_api.py tests/test_csrf.py tests/test_concurrency_and_resources.py tests/test_ratelimit_contract.py tests/test_cli.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G1 | ~~A Valkey outage is not translated to a `503`~~ | Closed in [06](06-mfa.md): sign-in, second factors and reset requests answer `503` with `Retry-After` and never let an attempt through uncounted | `test_mfa_api.py::TestRateLimiterOutage` |
| G2 | No multi-factor authentication | A password alone signs in; a reset needs only the mailbox | M9 (phase 06) |
| G6 | SMTP over TLS untested | The production mail path is unproven | M4, a TLS mail server in CI |
| G7 | No real-browser test of cookies and CSRF | Browser behaviour is assumed from specifications | M13, Playwright |
| G12 | Email addresses stored in plain text in `users` and `invitations` | Readable to anyone with database access | Consider encryption with a blind index |
| — | ~~An administrator can disable any account, including their own and the last administrator's; there is no re-enable endpoint~~ | Closed: your own account and the last enabled admin are refused (`409`), race-safe under the admin row lock; `POST /admin/users/{id}/enable` re-enables, with a recent sign-in, audited (D5.15, T5.7.7 to T5.7.11) | `TestAccountAdministration`, `TestLastAdminUnderConcurrency` |
| — | `EmailTakenError`'s docstring mentions open invitations, but `invite` checks existing accounts only | Inviting one email twice creates two valid invitations; the second registration then fails with `409` | Refuse or replace an open invitation for the same email |
| — | ~~`POST /auth/reauthenticate` refreshes `authenticated_at` but does not rotate the session token~~ | Closed: step-up rotates the token as [`architecture.md` §5.2](../architecture.md#52-sessions) says, keeping the level and the 24-hour limit (D5.7, T5.8.8) | `TestReauthentication` |
| — | ~~The per-client throttle key is uvicorn's client address; uvicorn trusts `X-Forwarded-For` from `127.0.0.1`~~ | Closed in the app: `HUMANI_T_TRUSTED_PROXIES` decides, empty by default, Caddy alone in compose (D5.6, T5.10.6). Uvicorn must still run with `--no-proxy-headers` (the API image and the development command), or it rewrites the address first | `TestClientAddress` |
| — | The screening blocklist has about fifty entries, all shorter than 15 characters; Have I Been Pwned is not offered even as opt-in | On its own the list only adds a reason to passwords already too short; it bites on repetitions such as `passwordpassword`. Common long passphrases pass | Ship a larger list of long breached passwords; opt-in HIBP (an outbound call, off by default) |
| — | `purge-expired` runs only by hand | The tables grow until someone runs it | Scheduled by the worker (M4) |

## Glossary

| Term | Meaning |
| --- | --- |
| **AAL2** | NIST "authenticator assurance level 2": the session rules followed here. |
| **Argon2id** | A memory-hard password hashing algorithm. |
| **CSRF** | Cross-site request forgery: another site making your browser send a request you did not intend. |
| **CSRF token** | A per-session secret our page echoes in `X-CSRF-Token`; another site cannot read it. |
| **Dummy hash** | A hash verified against when the account does not exist, so that path costs the same as a wrong password. |
| **Enumeration** | Learning which accounts exist from differences in responses or timing. |
| **Fetch Metadata** | Browser headers such as `Sec-Fetch-Site` that say where a request came from. |
| **`__Host-` cookie** | A cookie the browser accepts only if it is `Secure`, `Path=/` and has no `Domain`. |
| **NFKC** | A Unicode normalisation form that makes visually equivalent characters identical. |
| **Optimistic locking** | Saving only if the record's version is unchanged since it was read; otherwise refusing. |
| **Race condition** | A bug that appears only when two operations interleave in an unlucky order. |
| **Session fixation** | An attack where the victim is made to use a session id the attacker knows; prevented by issuing a new token at sign-in. |
| **Single-use claim** | A conditional update that succeeds for exactly one caller. |
| **Sliding window** | A rate limit counted over the last N minutes from now, not over fixed clock intervals. |
| **Step-up / re-authentication** | Confirming the password again before a sensitive action. |
| **Throttling vs lockout** | Slowing attempts down for a while vs blocking the account until someone intervenes. |
| **Valkey** | The open-source fork of Redis used for rate limiting. |
