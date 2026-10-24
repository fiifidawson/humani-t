# API Reference — v1

Interactive docs at `/docs` (Swagger) and `/redoc` when the server is running.

```bash
cd services/api
uv run uvicorn app.main:app --reload --port 8731
```

Then <http://127.0.0.1:8731/docs>.

> Port 8731 rather than 8000: if `mkdocs serve` is running for `docs-site/`
> it already holds 8000, and on Windows both processes can bind it without either
> reporting an error — uvicorn claims to be running while the browser shows the
> docs site.

**Status: walking skeleton.** Search is served by an in-memory corpus. No request
leaves the machine. Making it live is a change to
[`app/api/dependencies.py`](../services/api/app/api/dependencies.py) alone — the
endpoints and payloads below do not change.

---

## Endpoints

| Method | Path | Tag | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | system | Liveness |
| `GET` | `/api/v1/ready` | system | Readiness: can this process serve requests |
| `GET` | `/api/v1/info` | system | Which implementations are wired in (not in production) |
| `GET` | `/api/v1/sources` | search | Repositories this process can reach |
| `POST` | `/api/v1/vocabulary/compile` | vocabulary | Vocabulary to Boolean query |
| `POST` | `/api/v1/search` | search | Compile, then search |
| `POST` | `/api/v1/auth/login` | auth | Sign in; sets the session and CSRF cookies |
| `POST` | `/api/v1/auth/logout` | auth | End this session |
| `GET` | `/api/v1/auth/session` | auth | Who is signed in |
| `POST` | `/api/v1/auth/reauthenticate` | auth | Confirm the password before a sensitive action |
| `POST` | `/api/v1/auth/register` | auth | Accept an invitation; signs in |
| `POST` | `/api/v1/auth/password-reset` | auth | Email a reset link (always 202) |
| `POST` | `/api/v1/auth/password-reset/confirm` | auth | Set a new password from a reset link |
| `GET` | `/api/v1/account/sessions` | account | Where this account is signed in |
| `DELETE` | `/api/v1/account/sessions/{id}` | account | Sign out one device |
| `POST` | `/api/v1/account/sessions/revoke-others` | account | Sign out every other device |
| `POST` | `/api/v1/account/password` | account | Change the password |
| `POST` | `/api/v1/admin/invitations` | admin | Invite someone by email |
| `POST` | `/api/v1/admin/users/{id}/disable` | admin | Disable an account |

---

### `GET /api/v1/health`

Answers *is this process up*, not *is the whole system healthy*. Touches no
provider and no database — conflating the two makes deploys hard to debug,
because a failing dependency then looks like a dead process.

```json
{ "status": "ok" }
```

---

### `GET /api/v1/ready`

Answers *can this process serve requests*: the database answers a query. The
reverse proxy uses it to hold traffic back while the database is down, without
restarting a process that is itself fine.

```json
{ "status": "ready" }
```

`503` with a problem document when a backing service is unreachable.

---

### `GET /api/v1/info`

Reports which implementations are active behind the interfaces. **Not mounted
in production**, and neither are `/docs`, `/redoc` or `/openapi.json`: they map
the system for anyone who asks.

```json
{
  "name": "humani-t API",
  "version": "0.1.0",
  "api_prefix": "/api/v1",
  "search_provider": "fake",
  "makes_network_calls": false
}
```

The whole design rests on implementations being swappable. This endpoint makes
the current choice visible instead of buried in a config file — and
`makes_network_calls` is the fastest way to confirm a deployment is not talking
to anyone it should not.

---

### `GET /api/v1/sources`

```json
[
  {
    "source_id": "fake",
    "live": false,
    "description": "In-memory corpus of four papers. No network, no key, no rate limit."
  }
]
```

One entry today. It becomes the list a reviewer checks to know what a run covered
— an unexpected entry here is the first sign a run is not reproducible.

---

### `POST /api/v1/vocabulary/compile`

Shows exactly what will be searched for, before anything is searched.

**Request**

```json
{
  "origin": "llm_assisted",
  "version": 1,
  "concepts": [
    {
      "term": "chatbot",
      "synonyms": ["conversational agent"],
      "theme": "technology"
    },
    {
      "term": "humanitarian",
      "synonyms": ["crisis response"],
      "theme": "setting"
    }
  ]
}
```

`origin` is `llm_assisted` or `user_supplied`. The latter marks the vocabulary
**strict**: used exactly as given, with no model-driven expansion.

**Response**

```json
{
  "rendered": "(\"chatbot\" OR \"conversational agent\") AND (\"humanitarian\" OR \"crisis response\")",
  "themes": [
    {
      "theme": "technology",
      "groups": [{ "concept": "chatbot", "terms": ["chatbot", "conversational agent"] }]
    },
    {
      "theme": "setting",
      "groups": [{ "concept": "humanitarian", "terms": ["humanitarian", "crisis response"] }]
    }
  ],
  "term_count": 4,
  "provenance": {
    "vocabulary_version": 1,
    "vocabulary_origin": "llm_assisted",
    "is_strict": false
  }
}
```

`rendered` is for humans — logs, review screens, the audit trail. It is **not**
what gets sent to a repository; each provider compiles the structured form into
its own dialect.

The endpoint is pure and deterministic: the same vocabulary always produces the
same query, and no model is consulted for either origin. That is what makes a
user-supplied vocabulary strict by construction rather than by discipline.

Invalid input returns `422` — Pydantic checks shape, the domain checks meaning.

---

### `POST /api/v1/search`

Compiles the vocabulary and runs it against the configured source.

**Request** — the compile body plus `limit` (1–200, default 25).

**Response**

```json
{
  "source_id": "fake",
  "query": { "rendered": "...", "themes": [], "term_count": 4, "provenance": {} },
  "total": 2,
  "results": [
    {
      "source_id": "fake",
      "external_id": "fake-001",
      "title": "Conversational agents for crisis response in humanitarian settings",
      "year": 2024,
      "doi": "10.1000/fake.001",
      "is_open_access": true,
      "abstract": "...",
      "url": "https://example.org/fake-001",
      "identity": "doi:10.1000/fake.001"
    }
  ]
}
```

Two things worth noticing.

**The query travels with the results.** A result set separated from the query
that produced it cannot be audited, and auditability is the point of the system.

**Every record carries an `identity`.** DOI when present, otherwise normalised
title plus year. It is the key that collapses the same paper arriving from four
sources into one record.

---

## Semantics

### How a query is built

```text
each Concept        ->  (term OR synonym OR synonym)
concepts in a theme ->  OR'd together
themes              ->  AND'd together
```

So a vocabulary with concepts in two themes requires a match in **both**. With
the example above, a paper about chatbots in education does not match, and
neither does one about humanitarian logistics.

### Strict mode

`origin: "user_supplied"` means the terms reach the providers exactly as given.
This is a correctness property with a test behind it
(`test_strict_vocabulary_gains_no_terms`), not a convention — when LLM expansion
is added, that test is what stops it reaching user-supplied terms.

---

## Authentication

Sessions are server-side. Signing in sets two cookies:

| Cookie | Holds | Readable by scripts | SameSite |
| --- | --- | --- | --- |
| `__Host-session` | the session token | no (`HttpOnly`) | `Lax` |
| `__Host-csrf` | the session's CSRF token | yes | `Strict` |

Every state-changing request made while signed in must send the CSRF token
back in an `X-CSRF-Token` header, or it is refused with `403`. Requests whose
`Origin` or `Sec-Fetch-Site` show they come from another site are refused
before that, as are request bodies that are not `application/json` (`415`).

Sessions end after 1 hour idle or 24 hours in total. Changing the password needs
a sign-in within the last 15 minutes; otherwise the response is `401` with
`"reauthentication_required": true`, and `POST /auth/reauthenticate` refreshes it.

Registration is by invitation only. The first administrator is created inside
the deployment: `python -m app.cli create-admin --email … --password-stdin`.

| Status | Meaning |
| --- | --- |
| `401` | not signed in, wrong credentials, or the session has ended |
| `403` | missing CSRF token, cross-site request, wrong current password, or not an admin |
| `422` | invalid body, or a password that breaks the policy (`violations` lists why) |
| `429` | too many attempts; `Retry-After` says when to try again |

---

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem
document, served as `application/problem+json`:

```json
{ "type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found" }
```

Validation errors (`422`) add an `errors` list of `{loc, msg, type}`. They never
echo the submitted value back, because it may be the sensitive text the user was
sending. Unexpected errors (`500`) say nothing about the cause; quote the
response's `x-request-id` header when reporting one.

Every response carries security headers (CSP, HSTS, `nosniff`,
`Cache-Control: no-store`, …) and an `x-request-id`.

---

## Not here yet

Deliberately absent, in build order:

- **Persistence of reviews** — only encryption keys are stored so far
- **Real sources** — arXiv, OpenAlex, Europe PMC, Google Scholar
- **Screening, synthesis, the privacy gate**
- **Multi-factor authentication** — passkeys, TOTP and recovery codes (next)
- **Projects and roles** — owner, reviewer, viewer

See [`dependencies.md`](./dependencies.md) for the sequence.
