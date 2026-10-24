# Implementation Plan — Sessions 1 to 3

Verified: OpenAlex is keyless, returns 4,560 results for a query in your domain, with DOI, year, and open-access flag. That's your first real integration.

Here's where to start, concretely.

---

## Session 1 — make it breathe (~30 min)

```
services/api/app/
├── __init__.py          empty
└── main.py              FastAPI() + one route
```

Target: `GET /api/v1/health` → `{"status": "ok"}`

```bash
cd services/api
uv run uvicorn app.main:app --reload
```

**What you'll see:** terminal says `Uvicorn running on http://127.0.0.1:8000`. Open `http://127.0.0.1:8000/docs` and there's a Swagger page with your one endpoint, a "Try it out" button, and a live response. You built that with six lines.

**Done when:** you can hit the button and get `{"status":"ok"}` back.

Three things will confuse you — the empty `__init__.py`, the `:` in `app.main:app`, and what `--reload` watches. Ask when you hit them.

---

## Session 2 — the shape, with the world switched off (~half a day)

Now the interesting part. You write the domain and one provider, and **nothing touches the network**.

```
app/
├── domain/
│   ├── vocabulary.py    Concept, Vocabulary
│   ├── query.py         BooleanQuery
│   └── paper.py         PaperRecord
└── search/
    ├── base.py          PaperSearchProvider (Protocol)
    └── fake.py          FakePaperSearchProvider
tests/
└── test_search.py
```

**The compilation rule** — this is the concrete thing your `BooleanQuery` encodes, and it's how real systematic reviews work:

```
each Concept        -> (term OR synonym OR synonym)
concepts in a theme -> OR'd together
themes              -> AND'd together

("chatbot" OR "conversational agent") AND ("humanitarian" OR "crisis response")
```

**`PaperRecord` needs a dedup identity now**, not later: DOI when present, else normalised `title + year`. You'll merge four sources into one set, and if identity is an afterthought you'll be deduplicating by string equality on titles forever.

The fake returns three hardcoded `PaperRecord`s. That's the point — it proves the interface without a network, a key, or a rate limit.

**Done when:** `uv run pytest` is green on a test that builds a `Vocabulary`, compiles it to a `BooleanQuery`, passes it to the fake, and gets `PaperRecord`s back.

**Watch for the signal:** if the fake is awkward to write, your interface is wrong. Fix it now — it costs nothing today and a migration in month four.

---

## Session 3 — touch the real world (~half a day)

```bash
uv add httpx
```

```
app/search/openalex.py     same Protocol, real HTTP
app/api/routes/search.py   POST /api/v1/search
app/api/dependencies.py    which provider gets injected
```

Two practical notes from the call I just made: pass `mailto=` on every request — it puts you in OpenAlex's polite pool with better rate limits. And compile into `title_and_abstract.search`, which accepts `AND`/`OR`/`NOT`, rather than the loose `search=` param.

**What you'll see:** `/docs` now has a search endpoint. Paste a vocabulary, hit Execute, and real papers come back — titles, DOIs, years, open-access flags.

**Then the moment that matters:** change one line in `dependencies.py` to return `FakePaperSearchProvider` instead. Same endpoint, same response shape, no network. Change it back. **That switch is your architecture working** — everything above `search/` is untouched, and it's the same move that turns PostgreSQL into SQLite in V2.

**Done when:** you've flipped between fake and real without editing anything outside `dependencies.py`.

---

## After that

You have a walking skeleton: HTTP → application → domain → provider → the internet, with a proven seam. Steps 6 through 17 in [dependencies.md](./dependencies.md) are filling that shape in — persistence, the other three sources, mmore, screening.

**Start today with Session 1.** It's thirty minutes and it converts the last two weeks of documents into something running.

One thing that is *not* blocking: the multi-tenancy question I asked. No auth and no database in these three sessions, so it can wait — but it needs answering before Session 4, when `Review` gets an owner.
