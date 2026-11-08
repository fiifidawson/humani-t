# 09 — Plan and discover

> **Milestones:** M11 · **Status:** done (the outbound layer, then the [planning conversation](#planning-conversation)) · **Previous:** [07 — Projects and roles](07-projects-and-roles.md) · **Next:** `10-masking` (planned)

This phase gives humani-t its way out to the world, and makes it safe. Every request to another server now goes through one SSRF-guarded fetcher that can only reach the hosts an operator configured, checks the address at the moment it connects, and follows redirects by hand. Behind it sit three real paper sources (OpenAlex, Europe PMC and arXiv), each compiling the same `BooleanQuery` into its own syntax and returning `PaperRecord`s stamped with where they came from, and the `InferenceProvider` seam with an Ollama implementation that only ever asks for JSON matching a schema. A `PromptGuard` stands in front of every model call and fails closed. It serves brief rules 1 and 10, [`architecture.md` §3.1](../architecture.md#31-inferenceprovider), [§3.7](../architecture.md#37-papersearchprovider) and [§5.6](../architecture.md#56-outbound-requests). The [planning conversation](#planning-conversation) that turns a question into an accepted plan is the second part of this file.

## What this phase delivers

- **An outbound fetcher that cannot be pointed inward.** A request to a host off the allowlist, to an internal or metadata address (in any spelling), or redirected to one, is refused before a byte is sent.
- **DNS rebinding cannot swap the address.** The name is resolved once per connection, every answer is checked, and the socket opens to the checked address while TLS and the Host header keep the name.
- **Credentials belong to one host.** A bearer token is attached by the destination it was configured for and never follows a redirect elsewhere.
- **Responses are bounded:** at most 10 MiB by default, with connect, read and total deadlines.
- **Three paper sources** behind `PaperSearchProvider`: `openalex`, `europepmc`, `arxiv`. Each keeps its source's published rate limit. Google Scholar is deliberately absent.
- **Model calls** through `InferenceProvider`, answered only with schema-valid JSON plus the model id and token usage, behind a `PromptGuard` that stops the call on a refusal or an error.
- **Settings refuse a hosted model API** (OpenAI, Anthropic, Google, ollama.com, Ollama `*-cloud` models…) unless the operator opts in, and refuse plain http to a model server outside the deployment in production.
- **Settings added:** `HUMANI_T_INFERENCE_URL`, `HUMANI_T_INFERENCE_MODEL`, `HUMANI_T_INFERENCE_TOKEN` (Docker secret `inference_token`), `HUMANI_T_INFERENCE_TIMEOUT_SECONDS`, `HUMANI_T_ALLOW_HOSTED_INFERENCE`, `HUMANI_T_PAPER_SOURCES`, `HUMANI_T_OPENALEX_API_KEY` (Docker secret `openalex_api_key`), `HUMANI_T_OPENALEX_MAILTO`.
- **No endpoints, tables, migrations or operator commands.** The providers are exposed on `Providers.paper_sources` and `Providers.inference` for the project search route and the run worker to use.

## Architecture choices and why

### D9.1 The guard sits where the socket is opened

- **Decision:** `HttpxFetcher` gives httpcore2 a network backend (`_GuardedNetwork`) whose `connect_tcp` looks the host up on the allowlist, resolves it once, refuses if any answer is a blocked address, and opens the TCP connection to that checked IP. httpcore2 then starts TLS with the original host name as the server name, and the request keeps its Host header.
- **Why:** an address check is only as good as the moment it runs. Checked earlier, the client resolves again when it connects, and a DNS server answering a public address to the check and `127.0.0.1` to the connection (DNS rebinding) walks through. Here there is no second lookup to win.
- **Rejected:** checking the URL's host before calling an ordinary client (rebinding, above); a forward proxy such as Smokescreen (another service to run and secure, for a check one function can make); pinning addresses in `/etc/hosts` (does not cover PDF hosts taken from paper metadata).
- **Costs:** it depends on httpcore2's network-backend interface, which is public but low-level. One bad address in a mixed answer refuses the whole name, so a host with a broken DNS record is unreachable rather than half-reachable.
- **Where:** [`app/net/httpx_fetcher.py`](../../services/api/app/net/httpx_fetcher.py).
- **Proven by:** `tests/test_net_fetcher.py::TestDnsRebinding`, `::TestBlockedAddresses`.

### D9.2 What counts as a blocked address

- **Decision:** anything but ordinary public unicast: Python's `is_global` is false, or it is multicast, reserved or unspecified, or it falls in CGNAT or `192.0.0.0/24` (named explicitly because cloud metadata lives there and `is_global` has changed between Python releases). IPv4-mapped and NAT64 addresses are judged by the IPv4 address they carry; 6to4 and Teredo are refused outright. Host literals are read the way C's `inet_aton` reads them, so `2130706433`, `0177.0.0.1`, `0x7f.1` and `127.1` are all `127.0.0.1`.
- **Why:** brief rule 10. PDF links come from paper metadata, which anyone can publish, and `169.254.169.254` or `fd00:ec2::254` would hand out cloud credentials. Resolvers accept the odd IPv4 spellings, so a check that only knew dotted decimal would miss them.
- **Rejected:** a deny-list of private ranges (misses metadata, CGNAT and embedded forms); allowing NAT64 outright (IPv6-only hosts with DNS64 need it for public addresses, but it can embed loopback).
- **Costs:** a legitimately published server on a 6to4 or Teredo address is unreachable. None of our sources uses one.
- **Where:** [`app/net/addresses.py`](../../services/api/app/net/addresses.py).
- **Proven by:** `tests/test_net_addresses.py`.

### D9.3 A fetcher is built for one purpose, with its destinations fixed

- **Decision:** `OutboundPolicy` (destinations, byte cap, timeouts, redirect limit) is given to a fetcher when it is built. Each paper source and the model server gets its own fetcher with exactly one `Destination`. Matching is exact: `api.openalex.org` does not allow `x.api.openalex.org`. A `Destination` may carry a bearer token; a `FetchRequest` may not carry `Authorization`, `Cookie`, `Proxy-Authorization` or `Host` at all.
- **Why:** a provider cannot widen its reach by asking, and a token cannot be attached to the wrong host, because the code that builds requests never holds it.
- **Rejected:** one shared fetcher with a per-request allowlist (any caller could pass a wider list); stripping credentials on cross-host redirects (works until someone adds a new credential header).
- **Costs:** one connection pool per source. The fetcher for PDF hosts, which come from metadata, will need a different allowlist rule; that arrives with full-text retrieval.
- **Where:** [`app/net/base.py`](../../services/api/app/net/base.py), [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py).
- **Proven by:** `tests/test_net_fetcher.py::TestAllowlist`, `::TestRedirects::test_a_bearer_token_is_sent_only_to_the_destination_it_belongs_to`, `tests/test_outbound_wiring.py`.

### D9.4 Redirects are followed by hand

- **Decision:** at most 5 hops; each new URL goes through the same scheme, allowlist and address checks; `303` (and `301`/`302` after a `POST`) become a `GET` without the body.
- **Why:** a redirect is a new request the server chose. Letting the HTTP client follow it would skip every check above.
- **Rejected:** disabling redirects altogether (arXiv and DOI resolvers use them).
- **Where:** `HttpxFetcher._follow_redirects`.
- **Proven by:** `tests/test_net_fetcher.py::TestRedirects`.

### D9.5 Bounded responses

- **Decision:** a declared `Content-Length` over the cap is refused before reading; otherwise the body is counted as it streams and cut off past the cap. Requests ask for `Accept-Encoding: identity`, so the cap measures what is held in memory. Connect 5 s, read 30 s and a total deadline of 60 s by default; the model server gets its own, longer limits.
- **Why:** a slow or endless response must not hold a worker, and a small compressed body must not expand past the cap.
- **Where:** `HttpxFetcher._read_capped`, `fetch`.
- **Proven by:** `tests/test_net_fetcher.py::TestLimits`.

### D9.6 Each source compiles the same query, and adds nothing

- **Decision:** each provider has a pure `compile_*_query(BooleanQuery) -> str`: themes AND'd, terms OR'd, every term one quoted phrase over title and abstract. A double quote or backslash inside a term becomes a space, so a term cannot close its phrase and smuggle in operators.
- **Why:** a strict vocabulary must reach every source exactly as the user wrote it ([`architecture.md` §4.3](../architecture.md#43-how-a-vocabulary-is-acquired)), and the exact string sent is part of what a reviewer needs to reconstruct a run.
- **Rejected:** escaping quotes with a backslash (each source escapes differently, and some not at all); refusing such terms (a quote is never searchable content, since every tokenizer drops it).
- **Costs:** OpenAlex stems phrases and arXiv searches title and abstract separately, so the three sources do not match identically. That is each source's behaviour, recorded rather than hidden.
- **Where:** [`app/search/openalex.py`](../../services/api/app/search/openalex.py), [`europepmc.py`](../../services/api/app/search/europepmc.py), [`arxiv.py`](../../services/api/app/search/arxiv.py), [`_remote.py`](../../services/api/app/search/_remote.py).
- **Proven by:** `tests/test_search_sources.py` (`test_a_strict_vocabulary_reaches_…_exactly_as_given`, `test_a_quote_inside_a_term_cannot_break_out_of_its_phrase`).

### D9.7 Rate limits are kept by pacing, one request at a time

- **Decision:** each source has a `Pacer`: requests wait their turn and start no sooner than the interval after the previous one finished. arXiv 3 s, OpenAlex 1 s without a key (0.2 s with one), Europe PMC 0.1 s.
- **Why:** arXiv's terms ask for one request every three seconds and one connection at a time; OpenAlex caps complex Boolean searches at 1/s without a key. Being blocked mid-run would leave a review half-searched.
- **Rejected:** retrying on 429 (still exceeds the limit first).
- **Costs:** the limit is per process. Several workers searching together can exceed it; see the gaps.
- **Where:** [`app/net/pacing.py`](../../services/api/app/net/pacing.py).
- **Proven by:** `tests/test_net_pacing.py`, `tests/test_search_sources.py::TestArxiv::test_requests_are_spaced_at_least_three_seconds_apart`.

### D9.8 A source that fails says so

- **Decision:** a network failure, an error status, a body that is not the expected format, Europe PMC's in-body `errMsg` and arXiv's error feed all raise `PaperSourceUnavailableError` naming the source. A record without a title is skipped.
- **Why:** an outage reported as "no papers found" under-reports the evidence, and nobody can tell afterwards.
- **Where:** [`app/domain/sources.py`](../../services/api/app/domain/sources.py), each provider.
- **Proven by:** `tests/test_search_sources.py::TestEverySource`.

### D9.9 Google Scholar is out; OpenAlex uses an API key

- **Decision:** no Scholar provider. OpenAlex takes an optional API key, sent as a bearer token by its fetcher, and an optional `mailto`.
- **Why:** Scholar has no API, and its `robots.txt` and Google's terms forbid automated queries. OpenAlex retired the `mailto` "polite pool" in February 2026: limits now depend on an API key, and without one every user behind the same IP shares about $0.10 of searches a day. `mailto` is still sent when set, as a contact address, because the owner asked for it; it no longer changes anything.
- **Rejected:** a Scholar scraper (breaks the terms, breaks without notice); putting the key in the URL (`api_key=` would land in logs).
- **Where:** [`docs/dependencies.md` §4](../dependencies.md#4-outside-services-and-the-libraries-that-reach-them).

### D9.10 Model calls ask for structured output, through Ollama's native API

- **Decision:** `InferenceProvider.chat(ChatRequest) -> ChatResult`. A request always carries a JSON schema for the answer; the result carries the parsed, validated answer, the model id and token usage. `OllamaInferenceProvider` posts to `/api/chat` with `format` set to the schema, `stream: false`, and temperature 0 by default, then validates the answer with `jsonschema` and refuses one cut off by the length limit.
- **Why:** free text cannot be checked, cited or stored as a decision, and the `RunManifest` must name the model. Ollama documents schema-constrained output only on its native `format` field; its OpenAI-compatible endpoint lists `response_format` but never the `json_schema` type, and a silently ignored schema would look like a bad model.
- **Rejected:** the OpenAI-compatible API (above); trusting the server's constraint without validating (Ollama's own docs say to validate).
- **Costs:** a wire format only Ollama speaks. vLLM or llama.cpp would be another implementation of the same Protocol.
- **Where:** [`app/domain/inference.py`](../../services/api/app/domain/inference.py), [`app/inference/`](../../services/api/app/inference/).
- **Proven by:** `tests/test_inference.py::TestOllama`, `::TestOutputValidation`, `::TestFakeInference`.

### D9.11 The prompt guard is in front of every call, and fails closed

- **Decision:** use cases get `GuardedInference`, never a bare provider. It passes the exact `ChatRequest` to a `PromptGuard`; a refusal raises `PromptRefusedError`, an error in the guard propagates, and in both cases nothing is sent. Today the guard is `FakePromptGuard()`, which allows everything; the masking lane replaces that one line in `api/dependencies.py`.
- **Why:** [`architecture.md` §3.9.1](../architecture.md#391-masking-is-derived-reviewable-and-re-runnable): a last scan of exactly what leaves. Putting it in the application layer means no provider can be written or configured without it.
- **Rejected:** a guard inside each provider (every new provider must remember it); failing open when the guard errors (a crashed leak check has checked nothing).
- **Where:** [`app/application/inference.py`](../../services/api/app/application/inference.py), [`app/inference/base.py`](../../services/api/app/inference/base.py).
- **Proven by:** `tests/test_inference.py::TestPromptGuard`.

### D9.12 The model server is the operator's, and settings check it

- **Decision:** the default is the compose service `http://ollama:11434`. Settings refuse, in every environment, a URL on a known hosted model API and any Ollama `*-cloud` model unless `HUMANI_T_ALLOW_HOSTED_INFERENCE=true`. In production, plain http is accepted only for a single-label name (a compose service), `localhost`, or a loopback, private, CGNAT or IPv6 unique-local address; anything else needs https. The fetcher entry for the model server skips the SSRF block, because the operator named it.
- **Why:** CLAUDE.md: never default a model call to a hosted API. A signed-in Ollama daemon forwards `*-cloud` models to ollama.com, so a local URL alone does not prove a local model. Masked text is still text about a humanitarian team's work.
- **Rejected:** checking only in production (the brief owner asked for production; the check costs nothing elsewhere and stops a development `.env` leaking real questions); resolving the host in settings to decide "internal" (settings never touch DNS).
- **Costs:** the hosted-domain list is not exhaustive. It catches the common mistake; the operator's choice of URL remains the real control. A dotted internal name such as `ollama.corp` needs https or a single-label alias.
- **Where:** [`app/settings.py`](../../services/api/app/settings.py), `inference_destination` in [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py).
- **Proven by:** `tests/test_settings.py::TestInferenceDestination`, `tests/test_outbound_wiring.py::TestInference`.

### D9.13 Nothing is contacted unless the operator lists it

- **Decision:** `HUMANI_T_PAPER_SOURCES` is empty by default; listed sources are built in the order given. Unknown or repeated names are refused at startup, and naming `scholar` says why.
- **Why:** the one rule: data leaves only to destinations the operator configured. Vocabulary terms are derived from a reviewer's question.
- **Where:** `app/settings.py`, `build_providers`.
- **Proven by:** `tests/test_settings.py::TestPaperSources`, `tests/test_outbound_wiring.py::TestPaperSources`.

### D9.14 Recorded responses, and one opt-in live test

- **Decision:** source tests replay real responses recorded on 2026-11-03 (`tests/fixtures/sources/`) through `FakeHttpFetcher`. The fetcher's own tests replace only the socket layer (`tests/net_support.py`), so address checks, redirects and limits run for real. `tests/test_live_sources.py` calls each live API once, and runs only with `HUMANI_T_LIVE_SOURCES=1`.
- **Why:** a test suite that calls the internet is slow, flaky and itself an outbound leak. A fake that returns a fixed list proves nothing; recorded bodies keep each source's real shape.
- **Costs:** recordings age. The live test is how we notice.

### D9.15 Reasoning models answer without thinking first (2026-11-08)

- **Decision:** the Ollama request carries `"think": false`.
- **Why:** reasoning models such as `qwen3.5` think before every answer by default. Only the schema-checked answer is kept, so the reasoning is discarded. On a laptop CPU, a five-term answer took 308 s with thinking and 11 s without it, and planning failed at the 300 s limit. Models that cannot think accept the flag (checked with `smollm2`).
- **Where:** [`app/inference/ollama.py`](../../services/api/app/inference/ollama.py).
- **Proven by:** `tests/test_inference.py::TestOllama::test_it_asks_a_reasoning_model_to_answer_without_thinking_first`.

### D9.16 OpenAI-compatible model servers, a hosted one as an opt-in (2026-11-08)

- **Decision:** `inference_api` chooses Ollama's API (the default) or OpenAI's Chat Completions, which covers OpenAI, Azure OpenAI, Mistral, Groq, vLLM and llama.cpp. A hosted host still needs `allow_hosted_inference`. The provider:
  - uses **strict structured output** whenever a schema allows it;
  - leaves out the keywords strict mode refuses (`minLength`, `maxLength`, `uniqueItems`), but **states them in the field's description** ("At most 300 characters."), and still enforces them on the answer;
  - **drops a sampling setting the model refuses** (GPT-5 takes only its default temperature) and remembers that for later calls;
  - **waits and retries on a rate limit**, up to three times, following `Retry-After` up to 30 s, then raises `InferenceRateLimitedError`.
- **Why:** a laptop CPU took one to three minutes per screened paper. Without the description, `gpt-5-mini` wrote 724-character criteria against a 300-character limit, failing two drafts in three.
- **Rejected and costs:** see [ADR 0010](../adr/0010-openai-compatible-models-as-an-opt-in.md).
- **Where:** [`app/inference/openai_compatible.py`](../../services/api/app/inference/openai_compatible.py), [`app/api/dependencies.py`](../../services/api/app/api/dependencies.py) (`inference_provider`).
- **Proven by:** `tests/test_inference_openai.py`, `tests/test_outbound_wiring.py::TestInference`, `tests/test_settings.py::TestInferenceDestination`.

### D9.17 A search that finds nothing stops the run at Discover (2026-11-08)

- **Decision:** when the sources answer but return no papers, Discover fails with "The search found no papers. Ask for changes to broaden it, then run it again."
- **Why:** the run used to carry on with nothing, and the brief reported it as a screening result. It happened when a masked place ("[PERSON_1]" for the Sahel) led the planner to invent an unmatched geography term.
- **Where:** [`app/application/pipeline.py`](../../services/api/app/application/pipeline.py) (`NOTHING_FOUND`).
- **Proven by:** `tests/test_pipeline_stages.py::TestDiscover::test_a_search_that_finds_nothing_stops_the_run_here_and_says_so`.

## Libraries

### httpx2 2.12.0 (with httpcore2 2.12.0)

- **What it does:** HTTP/1.1 client. We use httpx2 to build requests (URL parsing, query encoding, headers) and decode responses, and its connection layer httpcore2 for the pool and TLS, with our own network backend underneath.
- **Why this one:** the maintained successor of httpx, by the same author, now maintained by Pydantic. Already in the project as a test dependency; moved to runtime. httpcore2's `network_backend` is the one place to intercept connects without forking.
- **Alternatives:** aiohttp (resolver hooks exist, but TLS and connection logic are harder to keep using the checked IP); urllib3 (synchronous); a forward proxy (another service).
- **Trade-offs:** we rely on a lower-level interface than most callers use. Environment proxies, `.netrc` and cookies are deliberately not used.
- **What we gave up:** HTTP/2 (off; one fewer protocol to reason about) and the convenience of `AsyncClient`.

### jsonschema 4.26.0

- **What it does:** validates a JSON document against a JSON Schema (draft 2020-12).
- **Why this one:** the reference Python implementation, widely used and maintained.
- **Alternatives:** fastjsonschema (lighter, but generates and executes Python code from the schema); hand-written checks per call site (each would drift).
- **Trade-offs:** four transitive dependencies (`attrs`, `referencing`, `jsonschema-specifications`, `rpds-py`, the last compiled Rust).
- **What we gave up:** nothing we use.

## Files to read, in order

Start from the address rule and work outwards to the composition root: each file only uses ideas from the ones before it.

1. [`services/api/app/net/addresses.py`](../../services/api/app/net/addresses.py): which addresses are blocked, and the `inet_aton` reading of host literals.
2. [`services/api/app/net/base.py`](../../services/api/app/net/base.py): `Destination`, `OutboundPolicy`, `FetchRequest` refusing credential headers, the error hierarchy, the `HttpFetcher` Protocol.
3. [`services/api/app/net/httpx_fetcher.py`](../../services/api/app/net/httpx_fetcher.py): `_GuardedNetwork.connect_tcp` (the guard), then `_follow_redirects`, `_send`, `_read_capped`.
4. [`services/api/app/net/pacing.py`](../../services/api/app/net/pacing.py) and [`fake.py`](../../services/api/app/net/fake.py).
5. [`services/api/app/search/_remote.py`](../../services/api/app/search/_remote.py): `phrase` and `fetch_paced`.
6. [`services/api/app/search/openalex.py`](../../services/api/app/search/openalex.py), [`europepmc.py`](../../services/api/app/search/europepmc.py), [`arxiv.py`](../../services/api/app/search/arxiv.py): each module docstring lists the API facts it relies on.
7. [`services/api/app/domain/inference.py`](../../services/api/app/domain/inference.py): the request, the result, the verdict, the errors.
8. [`services/api/app/inference/base.py`](../../services/api/app/inference/base.py), [`schema.py`](../../services/api/app/inference/schema.py), [`ollama.py`](../../services/api/app/inference/ollama.py) (the module docstring holds D9.10), [`fake.py`](../../services/api/app/inference/fake.py).
9. [`services/api/app/application/inference.py`](../../services/api/app/application/inference.py): the guard in front of the model.
10. [`services/api/app/settings.py`](../../services/api/app/settings.py): `_inference_stays_with_the_operator`, `_paper_sources_are_known`.
11. [`services/api/app/api/dependencies.py`](../../services/api/app/api/dependencies.py): `paper_source_destination`, `inference_destination`, `_inference` and the placeholder guard.
12. [`services/api/tests/net_support.py`](../../services/api/tests/net_support.py): how the tests see where a connection really went.

## Set up

New settings must be in your `services\api\.env`. `scripts/dev_secrets.py` never overwrites, so either add the lines from `.env.example` by hand, or regenerate the file; your secrets in `secrets\` are kept and reused:

```powershell
Remove-Item services\api\.env
python scripts\dev_secrets.py
```

The dev `.env` enables all three sources and points at `http://127.0.0.1:11434`. For the model tests (T9.10 to T9.12), install Ollama from https://ollama.com/download, then:

```powershell
ollama pull llama3.1:8b
ollama serve
```

Leave `ollama serve` running in its own window (on Windows the tray app may already be serving; `ollama list` answers if so). Never run `ollama signin` on a machine that handles real data: it lets the daemon forward `*-cloud` models to ollama.com.

Every manual test below runs from `services\api`. Scripts are piped to Python through a PowerShell here-string, so keep them ASCII-only.

```powershell
cd services\api
```

## Manual tests

### T9.1 The automated outbound suite passes

- **Do:** `uv run pytest tests/test_net_addresses.py tests/test_net_fetcher.py tests/test_net_pacing.py tests/test_search_sources.py tests/test_inference.py tests/test_outbound_wiring.py tests/test_live_sources.py -v`
- **Expect:** every test passes; the three in `test_live_sources.py` are reported as skipped ("live network test").
- **Proves:** D9.1 to D9.14 hold without any network.
- **If it fails:** read the first failure in full; a failure in `test_net_fetcher.py` means the guard itself is broken, and nothing else should be trusted until it passes.

### T9.2 A metadata address is refused in every spelling, without a connection

- **Do:**

```powershell
@'
import asyncio
from app.net.base import Destination, OutboundPolicy, FetchRequest, OutboundRefusedError
from app.net.httpx_fetcher import HttpxFetcher

async def main():
    for host in ["169.254.169.254", "2852039166", "0251.0376.0251.0376", "[::ffff:169.254.169.254]", "localhost"]:
        bare = host.strip("[]")
        fetcher = HttpxFetcher(OutboundPolicy(destinations=(Destination(bare),), connect_timeout_seconds=2))
        try:
            await fetcher.fetch(FetchRequest("https://" + host + "/latest/meta-data/"))
            print(host, "-> FETCHED (bug)")
        except OutboundRefusedError as exc:
            print(host, "->", type(exc).__name__)
        finally:
            await fetcher.close()

asyncio.run(main())
'@ | uv run python -
```

- **Expect:** printed at once, each preceded by an `outbound_refused` warning log line: `BlockedAddressError` for `169.254.169.254`, `2852039166` (the same address in decimal), `[::ffff:169.254.169.254]` and `localhost`; `DestinationNotAllowedError` for `0251.0376.0251.0376`, because httpx2's URL parser rejects the octal spelling before the address check is reached (the check would refuse it too: `tests/test_net_addresses.py::TestHostLiterals`). `localhost` goes through your system resolver and is refused because it answers a loopback address.
- **Proves:** D9.1 and D9.2. Each host is allowlisted on purpose, as a careless operator might; the address check still refuses it.
- **If it fails:** `FETCHED` or a timeout means a connection was attempted. Stop and check `_GuardedNetwork.connect_tcp` and `is_blocked_address`.

### T9.3 A host off the allowlist is refused before any lookup

- **Do:** as T9.2, but with `Destination("api.openalex.org")` and the URL `https://example.org/`; or run `uv run pytest tests/test_net_fetcher.py::TestAllowlist -v`.
- **Expect:** `DestinationNotAllowedError`, immediately, even offline.
- **Proves:** D9.3.
- **If it fails:** check `HttpxFetcher._check` and `OutboundPolicy.destination_for`.

### T9.4 A real request through the guard (needs internet)

- **Do:**

```powershell
@'
import asyncio
from app.net.base import Destination, OutboundPolicy, FetchRequest
from app.net.httpx_fetcher import HttpxFetcher

async def main():
    fetcher = HttpxFetcher(OutboundPolicy(destinations=(Destination("export.arxiv.org"),)))
    try:
        response = await fetcher.fetch(FetchRequest("https://export.arxiv.org/api/query", params=(("search_query", 'ti:"humanitarian"'), ("max_results", "1"))))
        print(response.status, response.headers.get("content-type"), len(response.body), "bytes")
    finally:
        await fetcher.close()

asyncio.run(main())
'@ | uv run python -
```

- **Expect:** `200 application/atom+xml; charset=utf-8` and a few thousand bytes.
- **Proves:** the guard lets an allowed public host through with TLS verified against its name (D9.1).
- **If it fails:** `FetchFailedError` with `ConnectError` means no route to the internet or a proxy is required (the fetcher ignores proxy variables on purpose); a certificate error means the system trust store is missing.

### T9.5 Search the three sources and see the provenance (needs internet)

- **Do:**

```powershell
@'
import asyncio
from app.domain.query import compile_vocabulary
from app.domain.vocabulary import Concept, Vocabulary, VocabularyOrigin
from app.net.base import Destination, OutboundPolicy
from app.net.httpx_fetcher import HttpxFetcher
from app.search import arxiv, europepmc, openalex

query = compile_vocabulary(Vocabulary(concepts=(Concept("chatbot", ("conversational agent",), theme="tech"), Concept("humanitarian", theme="setting")), origin=VocabularyOrigin.USER_SUPPLIED))

async def main():
    for module, build, compile_ in [(openalex, openalex.OpenAlexProvider, openalex.compile_openalex_query), (europepmc, europepmc.EuropePmcProvider, europepmc.compile_europepmc_query), (arxiv, arxiv.ArxivProvider, arxiv.compile_arxiv_query)]:
        fetcher = HttpxFetcher(OutboundPolicy(destinations=(Destination(module.HOST),)))
        try:
            print("==", module.SOURCE_ID, "sent:", compile_(query))
            for r in await build(fetcher).search(query, limit=2):
                print("  ", r.source_id, r.external_id, r.year, r.doi, "|", r.title[:60])
        finally:
            await fetcher.close()

asyncio.run(main())
'@ | uv run python -
```

- **Expect:** three blocks, each starting with the exact query string sent to that source, then two records stamped with that source's id and its own identifier (`W…` for OpenAlex, `MED:…` or `PPR:…` for Europe PMC, `2xxx.xxxxxv1` for arXiv). The arXiv block arrives last, after its pacing.
- **Proves:** D9.6 (each source's dialect, terms unchanged) and D9.8 (provenance on every record).
- **If it fails:** `PaperSourceUnavailableError: openalex: HTTP 429` or `409` means the shared keyless OpenAlex budget is spent; configure `HUMANI_T_OPENALEX_API_KEY` and use `pacer=openalex.pacer_for(has_api_key=True)`.

### T9.6 The live smoke test (needs internet)

- **Do:** `$env:HUMANI_T_LIVE_SOURCES = '1'; uv run pytest tests/test_live_sources.py -v; Remove-Item Env:HUMANI_T_LIVE_SOURCES`
- **Expect:** `3 passed`.
- **Proves:** the recorded fixtures still match what the sources return (D9.14).
- **If it fails:** an arXiv failure right after T9.5 is arXiv refusing a second request within 3 s from another process (gap G9.4); wait ten seconds and rerun. Otherwise compare the live response with the fixture in `tests/fixtures/sources/`; re-record it and update the mapping and its tests together.

### T9.7 A hosted model API is refused at startup

- **Do:** `$env:HUMANI_T_INFERENCE_URL = 'https://api.openai.com/v1'; uv run python -c "from app.settings import Settings; Settings()"; Remove-Item Env:HUMANI_T_INFERENCE_URL`
- **Expect:** a `ValidationError` ending `inference_url points at a hosted model API (api.openai.com); set allow_hosted_inference to send text to a third party`.
- **Proves:** D9.12.
- **If it fails:** check `_HOSTED_INFERENCE_DOMAINS` and `_inference_stays_with_the_operator` in `settings.py`.

### T9.8 The operator can opt in, explicitly

- **Do:** `$env:HUMANI_T_INFERENCE_URL = 'https://api.openai.com/v1'; $env:HUMANI_T_ALLOW_HOSTED_INFERENCE = 'true'; uv run python -c "from app.settings import Settings; print(Settings().inference_url)"; Remove-Item Env:HUMANI_T_INFERENCE_URL; Remove-Item Env:HUMANI_T_ALLOW_HOSTED_INFERENCE`
- **Expect:** `https://api.openai.com/v1`.
- **Proves:** the refusal is a default the operator can override, not a hard ban (D9.12).

### T9.9 An Ollama cloud model is refused, and so is plain http to a remote host in production

- **Do:** `$env:HUMANI_T_INFERENCE_MODEL = 'gpt-oss:120b-cloud'; uv run python -c "from app.settings import Settings; Settings()"; Remove-Item Env:HUMANI_T_INFERENCE_MODEL`, then `uv run pytest tests/test_settings.py::TestInferenceDestination -v`.
- **Expect:** a `ValidationError` naming `gpt-oss:120b-cloud` as an Ollama cloud model; then every `TestInferenceDestination` test passes, including `test_production_refuses_plain_http_to_a_host_outside_the_deployment`.
- **Proves:** D9.12.

### T9.10 A real model call returns schema-valid JSON with its provenance (needs Ollama)

- **Do:**

```powershell
@'
import asyncio
from app.api.dependencies import build_providers
from app.domain.inference import ChatMessage, ChatRequest, ChatRole
from app.settings import Settings

schema = {"type": "object", "properties": {"include": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["include", "reason"], "additionalProperties": False}
request = ChatRequest(messages=(ChatMessage(ChatRole.SYSTEM, "Decide whether the paper is about chatbots in humanitarian settings. Answer as JSON."), ChatMessage(ChatRole.USER, "Title: A chatbot for asylum-seeking migrants in Europe.")), output_schema=schema)

async def main():
    providers = build_providers(Settings())
    try:
        result = await providers.inference.chat(request)
        print(dict(result.output))
        print(result.model_id, result.usage)
    finally:
        await providers.close()

asyncio.run(main())
'@ | uv run --env-file .env python -
```

- **Expect:** a dictionary with exactly `include` (a boolean) and `reason` (a string), then `llama3.1:8b TokenUsage(prompt_tokens=…, completion_tokens=…)` with non-zero counts. The first call can take a minute while the model loads.
- **Proves:** D9.10, end to end through the guarded fetcher with the operator's destination.
- **If it fails:** `InferenceUnavailableError: the model server does not have 'llama3.1:8b'` means the model is not pulled; `could not be reached: FetchFailedError` means Ollama is not running (T9.11).

### T9.11 No model server is a clear error, not a hang

- **Do:** stop Ollama (quit the tray app or `ollama serve`), then rerun T9.10.
- **Expect:** within a few seconds, `InferenceUnavailableError: the model server could not be reached: FetchFailedError`.
- **Proves:** connection failures are reported as the model being unavailable (D9.10), bounded by the connect timeout (D9.5).

### T9.12 A guard refusal stops the call before anything is sent

- **Do:**

```powershell
@'
import asyncio
from app.application.inference import GuardedInference
from app.domain.inference import ChatMessage, ChatRequest, ChatRole, PromptRefusedError
from app.inference.fake import FakePromptGuard
from app.inference.ollama import OllamaInferenceProvider
from app.net.base import Destination, OutboundPolicy
from app.net.httpx_fetcher import HttpxFetcher

async def main():
    fetcher = HttpxFetcher(OutboundPolicy(destinations=(Destination("127.0.0.1", allows_private_addresses=True, allows_plain_http=True),)))
    provider = OllamaInferenceProvider(fetcher, base_url="http://127.0.0.1:9", model="none")
    guarded = GuardedInference(provider, FakePromptGuard(refusal="1 unmasked email address"))
    request = ChatRequest(messages=(ChatMessage(ChatRole.USER, "Contact amara@example.org"),), output_schema={"type": "object"})
    try:
        await guarded.chat(request)
    except PromptRefusedError as exc:
        print("refused:", exc)
    finally:
        await fetcher.close()

asyncio.run(main())
'@ | uv run python -
```

- **Expect:** `refused: 1 unmasked email address`, at once. Port 9 has nothing listening, so if a request had been attempted you would see `InferenceUnavailableError` instead.
- **Proves:** D9.11: the guard runs first and fails closed.
- **If it fails:** an `InferenceUnavailableError` means the call reached the network before the guard; check `GuardedInference.chat`.

### T9.13 The API refuses to start with a hosted model server

- **Do:** `$env:HUMANI_T_INFERENCE_URL = 'https://api.anthropic.com'; uv run --env-file .env uvicorn app.main:app --port 8731 --no-proxy-headers`, then stop it with Ctrl+C if it started, and `Remove-Item Env:HUMANI_T_INFERENCE_URL`.
- **Expect:** uvicorn exits during startup with a `ValidationError` naming `api.anthropic.com`. A value set in the shell wins over `.env`.
- **Proves:** the check runs where the operator will meet it, at startup (D9.12).

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T9.1 | ☐ | |
| T9.2 | ☐ | |
| T9.3 | ☐ | |
| T9.4 | ☐ | |
| T9.5 | ☐ | |
| T9.6 | ☐ | |
| T9.7 | ☐ | |
| T9.8 | ☐ | |
| T9.9 | ☐ | |
| T9.10 | ☐ | |
| T9.11 | ☐ | |
| T9.12 | ☐ | |
| T9.13 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the command, the output and, for T9.13, the API log line. It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Internal, metadata and special addresses are blocked; public ones are not | `tests/test_net_addresses.py::TestBlockedAddresses` |
| Odd IPv4 spellings and IPv6 literals are read as the address they mean | `tests/test_net_addresses.py::TestHostLiterals` |
| Only allowlisted hosts, exact match, https unless operator-named internal | `tests/test_net_fetcher.py::TestAllowlist` |
| A name resolving to a blocked address is refused, with no connection | `tests/test_net_fetcher.py::TestBlockedAddresses` |
| The socket opens to the checked address; Host and SNI keep the name | `tests/test_net_fetcher.py::TestDnsRebinding` |
| Redirects re-checked, at most 5, tokens never forwarded | `tests/test_net_fetcher.py::TestRedirects` |
| Byte cap, read timeout and total deadline | `tests/test_net_fetcher.py::TestLimits` |
| Callers cannot attach credentials; destinations are bare hosts | `tests/test_net_fetcher.py::TestRequests`, `::TestPolicy` |
| Pacing spaces and serialises requests | `tests/test_net_pacing.py` |
| Each source's dialect, mapping, errors and pacing | `tests/test_search_sources.py` |
| The same paper from two sources collapses into one | `tests/test_search_sources.py::TestAcrossSources` |
| Structured output validated; Ollama request shape, errors and usage | `tests/test_inference.py::TestOllama`, `::TestOutputValidation` |
| The guard refuses or fails closed | `tests/test_inference.py::TestPromptGuard` |
| Hosted APIs, cloud models and remote plain http refused by settings | `tests/test_settings.py::TestInferenceDestination` |
| Sources only when listed; tokens only to their own host | `tests/test_outbound_wiring.py`, `tests/test_settings.py::TestPaperSources` |
| No HTTP client outside `app.net` | `uv run lint-imports` |

How to run only this phase's tests:

```powershell
uv run pytest tests/test_net_addresses.py tests/test_net_fetcher.py tests/test_net_pacing.py tests/test_search_sources.py tests/test_inference.py tests/test_outbound_wiring.py tests/test_settings.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G9.1 | ~~The search-planning conversation does not exist~~ | Closed | [Planning conversation](#planning-conversation) |
| G9.2 | The prompt guard allows everything | Unmasked text could reach the model server | Masking lane (`10-masking`) replaces `FakePromptGuard()` in `api/dependencies.py` |
| G9.3 | `fetch_fulltext` returns `None` for every source | Screening works on abstracts only | Full-text retrieval and extraction (build order step 10), with a PDF-host fetcher policy |
| G9.4 | Pacing is per process | Several workers searching at once can exceed a source's rate limit | A shared limiter in Valkey when more than one process searches |
| G9.5 | No retry on 429 or 5xx | One busy moment fails a source for the whole run | A bounded retry honouring `Retry-After`, once runs report per-source status |
| G9.6 | ~~The exact compiled query per source is not yet stored~~ (closed by the `queries` stage's manifest) | A reviewer can recompute it (`compile_*_query` is pure) but not read it from the run | Record it in the `RunManifest` with the search results |
| G9.7 | `PaperSourceUnavailableError` and `InferenceUnavailableError` are not mapped to HTTP | A route that lets them escape answers 500 | Map to 502 in `api/errors.py` when a route calls them |
| G9.8 | Europe PMC's rate limit and some response details are confirmed from a forum and live calls, not its documentation | The documented limit could differ | Recheck when the documentation pages can be read |
| G9.9 | Ollama answers in tests are shaped from the documentation, not recorded | A field rename would pass the tests | T9.10 by hand; an opt-in live inference test like `test_live_sources.py` |
| G9.10 | Outbound refusals are logged, not audited | An SSRF attempt leaves a log line only | Revisit with the threat model (M14) |

## Glossary

| Term | Meaning |
| --- | --- |
| **SSRF** | Server-side request forgery: tricking a server into making a request on the attacker's behalf, typically to an internal address it can reach and the attacker cannot. |
| **DNS rebinding** | A domain that answers one address when checked and another (often `127.0.0.1`) when connected to, to slip past a check made before the connection. |
| **Cloud metadata endpoint** | An address inside cloud VMs (`169.254.169.254`, `fd00:ec2::254`) that hands out instance credentials to whoever asks. |
| **CGNAT** | Carrier-grade NAT, `100.64.0.0/10`: shared address space used by ISPs and VPNs such as Tailscale. Not public. |
| **IPv4-mapped / NAT64 / 6to4 / Teredo** | IPv6 forms that carry an IPv4 address inside them, so an IPv6 check alone can miss a private IPv4 target. |
| **SNI** | Server Name Indication: the host name a TLS client presents, which the server uses to pick its certificate and the client uses to verify it. |
| **Allowlist / Destination** | The hosts one fetcher may reach, and per host whether plain http, private addresses and a bearer token are allowed. |
| **Pacer** | Waits so requests to one source start no sooner than its interval after the previous one finished, one at a time. |
| **Structured output** | A model answer constrained to, and validated against, a JSON schema. |
| **Prompt guard** | The last check on the exact text leaving for a model; refuses rather than repairs, and fails closed. |
| **Hosted inference** | A model run by a third party (OpenAI, Anthropic, ollama.com…), as opposed to a server the operator runs. |
| **Polite pool** | OpenAlex's former higher rate limit for requests carrying `mailto`, replaced by API keys in 2026. |

---

## Planning conversation

> **Status:** done · the second part of this phase · **Previous part:** the outbound layer above

This part turns a review's question into an accepted, frozen search plan: its vocabulary and its screening criteria, in numbered versions. In deep mode the model asks 2 to 4 clarifying questions first; then it drafts themes, concepts with synonyms, and criteria; a person asks for changes in plain words and the model drafts the next version; a person accepts one, and it is frozen. Or a person uploads their own terms, which are parsed by code and used exactly as given, and the model is never called. Every piece of a person's text is masked before any model call, and each call leaves a privacy receipt of exactly what the model received. The run's `queries` stage compiles the accepted plan into each source's query, and stops at a `vocabulary` gate when there is none. It serves brief rules 1, 9 and 11, [`architecture.md` §4.3](../architecture.md#43-how-a-vocabulary-is-acquired) and UI_UX_SPEC §5.3.1 to 5.3.4, §6.3 to 6.6 and §11.1.

### What this part delivers

- **Deep or quick.** `POST …/plan {"mode":"deep"}` asks clarifying questions; `{"mode":"shallow"}` drafts at once. Answers are kept one at a time, and the last one (or "skip all and draft now") drafts version 1.
- **Changes make versions.** `POST …/plan/changes` records the request, then the model drafts the next version; each version stores what changed against the one before and who wrote it.
- **The first accept wins.** `POST …/plan/accept` freezes the vocabulary at that version; a second accept gets `409` and the first stands.
- **Your own terms, exactly.** `PUT …/plan/vocabulary` takes a documented text or CSV format. No model, no sanitiser, nothing leaves; a mistake is refused with its line number, never repaired.
- **Masking before every model call, and a receipt after.** The question, answers and change requests go through the `Sanitizer`; the receipt shows the masked text and the counts by type, and says when a failed call may still have been read.
- **The `queries` stage** compiles the accepted plan per enabled source and records the exact query strings in its summary's `manifest`. Without an accepted plan it drafts one if needed, then asks for the `vocabulary` gate.
- **Endpoints:** `GET` and `POST /api/v1/reviews/{review_id}/plan`, `POST …/plan/answers`, `POST …/plan/changes`, `POST …/plan/accept`, `PUT …/plan/vocabulary`, `GET …/plan/versions/{version}` ([`api.md`](../api.md#search-plans)).
- **Migration** `0008`: three tables and a trigger. No settings and no operator commands.

#### Data model

| Table | One row per | In the clear | Sealed under the project's key |
| --- | --- | --- | --- |
| `plan_conversations` | review | mode, who started it and when, version (optimistic lock) | clarifying questions and answers, the pending change request |
| `plan_versions` | review and version | origin, author kind and id, created, accepted at and by | concepts, criteria, notes, the change request and answers it came from, the drafting model and prompt version, the diff |
| `plan_receipts` | model call | purpose, model id, prompt version, delivered, who and when | the masked text sent and its counts |

`plan_versions` refuses a `user_supplied` row written by the model (a check constraint), and a trigger refuses every update except recording the acceptance of a version not yet accepted.

### Architecture choices and why

#### D9.15 Versions are written once; accepting is the only update

- **Decision:** each version is an insert keyed by `(review_id, version)`; accepting is one conditional `UPDATE` (latest and not yet accepted). A trigger refuses any other change. Runs use the latest accepted version. See [ADR-0005](../adr/0005-plan-versions-are-written-once.md).
- **Why:** a run must be reproducible from the version it recorded, and two people accepting at once must not both win (UI_UX_SPEC §11.2).
- **Rejected:** one mutable plan row with a counter (history lost, overwrites possible); accepting by reading then writing (two winners under concurrency).
- **Costs:** every change is a new row.
- **Where:** [`app/domain/plans.py`](../../services/api/app/domain/plans.py), [`app/storage/postgres/plans.py`](../../services/api/app/storage/postgres/plans.py), [`migrations/versions/20261103_0008_plans.py`](../../services/api/migrations/versions/20261103_0008_plans.py).
- **Proven by:** `tests/test_plans_storage_contract.py::TestAccept`, `::TestPostgresUnderConcurrency`, `::TestPostgresSchema`, `tests/test_plans_api.py::TestAccept`.

#### D9.16 One version number for the vocabulary and the criteria

- **Decision:** a `ReviewPlan` holds both, and its version is theirs: `vocabulary_version` and `criteria_version` in the manifest are the plan's version.
- **Why:** a person accepts a plan, not two halves of one; one number is one thing to record and compare.
- **Rejected:** separate counters (two numbers for a distinction no screen needs).
- **Costs:** a criteria-only change also bumps the vocabulary version.
- **Where:** `ReviewPlan` in [`app/domain/plans.py`](../../services/api/app/domain/plans.py).
- **Proven by:** `tests/test_plans_domain.py::TestReviewPlan::test_the_vocabulary_carries_the_plans_version`.

#### D9.17 Route B is parsed by code, refused rather than repaired, and never reaches the model

- **Decision:** two documented formats (text and CSV). Terms are trimmed and otherwise kept exactly; an empty or repeated term is refused with its line number. The upload calls neither the model nor the sanitiser, and no version holding a user-supplied vocabulary may be written by the model (domain rule and database constraint). The model's change requests are refused on such a plan.
- **Why:** "strict means strict" (architecture §4.3, rule 3): a researcher with a validated list must get exactly that list back.
- **Rejected:** silently dropping duplicates (a silent normalisation); asking the model to tidy the list (route B would route through the model).
- **Costs:** a sloppy file is refused until it is fixed.
- **Where:** `parse_strict_vocabulary` in [`app/domain/plans.py`](../../services/api/app/domain/plans.py); `upload_vocabulary` in [`app/application/planning.py`](../../services/api/app/application/planning.py).
- **Proven by:** `tests/test_plans_domain.py::TestStrictTextFormat::test_a_strict_vocabulary_compiles_to_exactly_the_supplied_terms`, `tests/test_plans_api.py::TestStrictUpload`, `tests/test_queries_stage.py::TestWithoutAnAcceptedPlan::test_a_strict_vocabulary_goes_through_the_gate_without_the_model`.

#### D9.18 Mask, call with no transaction open, then write what was read again

- **Decision:** each use case reads in a short transaction, masks and calls the model with none open, then writes in a second short transaction that re-checks the review's status and the version it was based on. A change request is recorded first (`changes_requested`), so other members see it while the model drafts and nobody accepts the draft being replaced.
- **Why:** architecture §5.7 rule 1: no connection held across slow work, and no blind overwrite.
- **Rejected:** one transaction around the model call (a pooled connection held for tens of seconds).
- **Costs:** a model failure after the request was recorded leaves the review in `changes_requested`; asking again retries.
- **Where:** [`app/application/planning.py`](../../services/api/app/application/planning.py).
- **Proven by:** `tests/test_plans_api.py::TestChanges::test_a_change_to_a_draft_that_has_moved_on_is_refused`, `TestStartingAPlan::test_an_unreachable_model_changes_nothing_but_says_the_text_may_have_left`.

#### D9.19 A receipt for every call that may have sent text

- **Decision:** after each model call, success or failure, a receipt records the masked text of each item sent (question, each answer, the change request) and the counts by type. A call the prompt guard refused sent nothing and has no receipt.
- **Why:** UI_UX_SPEC §5.3.2 and §8.5: people see what left, and a failed call may still have been read.
- **Rejected:** a receipt only on success (it would hide text that left); a receipt before the call (it would claim refused calls were sent).
- **Costs:** one more short transaction per call.
- **Where:** `_ask` and `_store_receipt` in [`app/application/planning.py`](../../services/api/app/application/planning.py).
- **Proven by:** `tests/test_plans_api.py::TestPrivacyReceipts`, `TestStartingAPlan::test_a_call_the_prompt_guard_refuses_sends_nothing_and_changes_nothing`.

#### D9.20 Who added a term comes from recorded authors only

- **Decision:** each version records its author (the model, or a person by id). A term's author is the author of the version that introduced it; the diff credits each change to the version's author.
- **Why:** origin is "recorded and surfaced, never inferred" (architecture §4.3, rule 2).
- **Rejected:** matching terms against the change request's text to credit the person who asked (a guess presented as provenance).
- **Costs:** a term a person asked the model for shows as the model's; only terms a person wrote (an upload, an edit at the gate) show as theirs.
- **Where:** `diff_plans` and `term_authors` in [`app/domain/plans.py`](../../services/api/app/domain/plans.py).
- **Proven by:** `tests/test_plans_domain.py::TestDiff`, `::TestTermAuthors`, `tests/test_plans_api.py::TestVersions`.

#### D9.21 The `queries` stage gates through the same accept

- **Decision:** with no accepted plan the stage drafts one if none exists (masked, like any other call), asks for the `vocabulary` gate with the draft as payload, and on approval accepts through the same conditional write as the API; revisions first become a version written by the person who decided. A rejection fails the run in plain words. Planning is refused while a run is going.
- **Why:** UI_UX_SPEC §6.6: a run started on an unconfirmed vocabulary asks for it; and a stage may run again after the gate, so it must find its own work.
- **Rejected:** failing the run when no plan is accepted (a dead end for the person who pressed Start).
- **Costs:** a run started on a brand-new review makes one model call inside the worker.
- **Where:** [`app/application/queries_stage.py`](../../services/api/app/application/queries_stage.py).
- **Proven by:** `tests/test_queries_stage.py`.

#### D9.22 Masked text never becomes a search term

- **Decision:** a model-drafted term holding a placeholder such as `[LOCATION_1]` is dropped and counted in the version's `notes`.
- **Why:** the plan's terms are sent to the paper sources; a placeholder would search for nonsense.
- **Rejected:** restoring the original value (unmasked text would leave for a paper source).
- **Costs:** a draft for a place-specific question may lose its place terms; a person can add them by uploading or at the gate.
- **Where:** `_draft_from` in [`app/application/planning.py`](../../services/api/app/application/planning.py).
- **Proven by:** `tests/test_plans_api.py::TestChanges::test_model_terms_that_hold_masked_text_are_dropped_and_noted`.

### Libraries

None added. The upload parser uses the standard library's `csv`.

### Files to read, in order

From the rules outward, because every later file only applies them.

1. [`services/api/app/domain/plans.py`](../../services/api/app/domain/plans.py): `ReviewPlan`'s invariants, `accepted` and `followed_by`, `diff_plans`, `term_authors`, `parse_strict_vocabulary`, and `PlanSource`.
2. [`services/api/app/domain/reviews.py`](../../services/api/app/domain/reviews.py): the review states; an accepted or finished review may now ask for changes.
3. [`services/api/app/storage/base.py`](../../services/api/app/storage/base.py) (`PlanRepository`), then [`memory_plans.py`](../../services/api/app/storage/memory_plans.py) and [`postgres/plans.py`](../../services/api/app/storage/postgres/plans.py): the conditional accept.
4. [`services/api/migrations/versions/20261103_0008_plans.py`](../../services/api/migrations/versions/20261103_0008_plans.py): the constraints and the trigger.
5. [`services/api/app/application/planning.py`](../../services/api/app/application/planning.py): the prompts and schemas at the top, then `start`, `answer`, `request_changes`, `accept`, `upload_vocabulary` and `_ask`.
6. [`services/api/app/application/queries_stage.py`](../../services/api/app/application/queries_stage.py): the gate and the manifest.
7. [`services/api/app/api/routes/plans.py`](../../services/api/app/api/routes/plans.py) and [`schemas/plans.py`](../../services/api/app/api/schemas/plans.py): status codes and shapes.

### Set up

Migrate to 0008, then run the API and, for T9.23 and T9.24, the worker. A model call needs Ollama, as set up for the first part.

```powershell
cd services\api
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

In Postman, sign in as the admin, create a project and a review as in T8.1.1, and note the review's id as `{{review}}`. Every write carries `X-CSRF-Token: {{csrf}}`.

### Manual tests

#### T9.14 The automated planning suite passes

- **Do:** `uv run pytest tests/test_plans_domain.py tests/test_plans_storage_contract.py tests/test_plans_api.py tests/test_queries_stage.py -v`
- **Expect:** every test passes; the Postgres ones are skipped unless `TEST_DATABASE_URL` is set.
- **Proves:** D9.15 to D9.22 hold with a fake model and a fake sanitiser.
- **If it fails:** a failure in `test_plans_domain.py` means a rule is broken; fix it before anything else.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T9.15 | `POST {{base}}/reviews/{{review}}/plan` `{"mode":"deep"}` | `201`; `status` `clarifying`; 2 to 4 `questions` with ids `q1`…; `current` null; one receipt with `purpose` `clarify`, whose `items[0].sent` is your question as masked | Deep mode, receipts (D9.19) | `502`: Ollama is not answering; `422`: the guard refused the question |
| T9.16 | `POST …/plan/answers` `{"answers":[{"question_id":"q1","answer":"Prices"}]}` | `200`; still `clarifying`; `q1.answer` set; no new receipt | Answers kept until all are settled | A draft already appeared: every question was settled |
| T9.17 | `POST …/plan/answers` `{"answers":[],"draft_now":true}` | `200`; `drafting`; `current.version` 1, `author.kind` `model`, `model_id` your model; a `draft` receipt with the question and answer items | Skip all and draft | `409`: the review was not `clarifying` |
| T9.18 | `POST …/plan/changes` `{"request":"add mobile money as a synonym for cash","based_on_version":1}`, then `GET …/plan/versions/2` | `200`; `current.version` 2 with `change_request` set; the diff lists each change `by` the model | Versions and diff (D9.15, D9.20) | `409` "draft changed": `based_on_version` was not the latest |
| T9.19 | Repeat T9.18 with `"based_on_version":1` | `409`, "The draft changed while you were looking at it." | Stale writes refused (D9.18) | `200` means stale changes overwrite newer ones |
| T9.20 | `POST …/plan/accept` `{"version":2}`, then the same again as Kofi | First `200`, `status` `accepted`, `accepted_version` 2; second `409` "Someone else already accepted this draft." | First accept wins (D9.15) | Two `200`s: the conditional write is broken |
| T9.21 | In pgAdmin as the app role: `UPDATE plan_versions SET body_sealed = 'x' WHERE version = 2;` | Error "a plan version is written once" | The trigger (D9.15) | The update succeeds: migration 0008 did not run |
| T9.22 | On a new review: `PUT …/plan/vocabulary` `{"format":"text","content":"[x]\ncash;;money\n"}`, then with `"content":"[intervention]\ncash transfers; vouchers\n[outcome]\nwasting\n"` | First `422` naming line 2; then `200`, `origin` `user_supplied`, `is_strict` true, `receipts` empty, and the Ollama window shows no request | Route B (D9.17) | A receipt or an Ollama request: the upload reached the model |

#### T9.23 A run asks for the vocabulary gate, then uses the accepted plan

- **Do:** start the worker (`uv run --env-file .env python -m app.worker`). On the review from T9.22, not yet accepted: `POST {{base}}/reviews/{{review}}/runs` `{"stages":["queries"]}`, follow `GET /runs/{run_id}/events`, then `POST /gates/{gate_id}` `{"decision":"approve"}`.
- **Expect:** `gate.required` with `kind` `vocabulary` and the draft as payload; after approval, `stage.finished` whose `summary.manifest` has `vocabulary_version`, `vocabulary_origin` `user_supplied`, and `compiled_queries` with one string per source in `HUMANI_T_PAPER_SOURCES`; `GET …/plan` now shows `accepted_version`.
- **Proves:** D9.21.
- **If it fails:** no gate means the plan was already accepted; a failed run mentioning the "search plan" means the gate was rejected.

#### T9.24 Planning waits for a run

- **Do:** start another run as in T9.23 on a review whose draft is not accepted; while it waits at its gate, `POST …/plan/changes` and `PUT …/plan/vocabulary`.
- **Expect:** `409` "The plan cannot change while a run is going."
- **Proves:** a run sees one plan from start to end (D9.21).
- **If it fails:** a `200` means a run could start with one plan and screen with another.

#### T9.25 Nothing is stored in the clear

- **Do:** in pgAdmin: `SELECT encode(body_sealed, 'escape') FROM plan_versions;` and the same for `plan_receipts` and `plan_conversations`.
- **Expect:** binary ciphertext only: no term, no question text, no placeholder.
- **Proves:** sealed under the project's key (architecture §5.4).
- **If it fails:** readable text is a leak; stop and report it.

#### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T9.14 | ☐ | |
| T9.15 | ☐ | |
| T9.16 | ☐ | |
| T9.17 | ☐ | |
| T9.18 | ☐ | |
| T9.19 | ☐ | |
| T9.20 | ☐ | |
| T9.21 | ☐ | |
| T9.22 | ☐ | |
| T9.23 | ☐ | |
| T9.24 | ☐ | |
| T9.25 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

### Automated tests that prove it

| Claim | Test |
| --- | --- |
| An accepted version is frozen and never accepted twice | `tests/test_plans_domain.py::TestReviewPlan` |
| A strict vocabulary compiles to exactly the supplied terms | `tests/test_plans_domain.py::TestStrictTextFormat::test_a_strict_vocabulary_compiles_to_exactly_the_supplied_terms` |
| The first accept wins, also concurrently; the database refuses rewrites | `tests/test_plans_storage_contract.py` |
| Question, answers and change requests are masked before any model call | `tests/test_plans_api.py::TestStartingAPlan`, `::TestAnswers`, `::TestChanges` |
| Each call leaves a receipt of exactly what was sent | `tests/test_plans_api.py::TestPrivacyReceipts` |
| Route B never reaches the model | `tests/test_plans_api.py::TestStrictUpload` |
| Plans are sealed, shredded with the project, and audited by id | `tests/test_plans_api.py::TestWhatIsStored` |
| The `queries` stage gates, accepts and records the compiled queries | `tests/test_queries_stage.py` |

How to run only this part's tests:

```powershell
uv run pytest tests/test_plans_domain.py tests/test_plans_storage_contract.py tests/test_plans_api.py tests/test_queries_stage.py -v
```

### Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G9.11 | Planning text is masked by the pattern detector only | Names and places in a question reach the model unmasked; only structured identifiers are masked | The masking lane wires its local NER detector into `_planning_sanitizer()` in `api/dependencies.py` |
| G9.12 | Project masking rules are not applied to planning text | A term the owner marked "always mask" is masked only if a detector also finds it | Wire the mask lane's rule store as the `MaskingRuleSource` in `api/dependencies.py` (`NO_PROJECT_RULES` today) |
| G9.13 | No automatic fitting to a source's limits, and no per-source "fits" indicator | A very long vocabulary fails at OpenAlex's URL limit during discover | A fit check per compiler, shown on the draft |
| G9.14 | A concept moved between themes shows as removed and added | The compare view has no "moved" marker (UI_UX_SPEC §6.5) | Detect moves when the canvas needs them |
| G9.15 | A draft can be edited directly only through the gate's revisions or an upload | No pill editing (UI_UX_SPEC §16, question 6) | A `PATCH` on the draft when the canvas allows direct edits |
| G9.16 | A failed revision leaves the review in `changes_requested` | The old draft cannot be accepted until the change is asked for again | Ask again to retry; a "drop this request" action if it proves annoying |

G9.1 (no planning conversation) and G9.6 (compiled queries not stored) are closed by this part.

### Glossary

| Term | Meaning |
| --- | --- |
| **Plan version** | One numbered `ReviewPlan`: a vocabulary and criteria, who wrote it, and what it came from. |
| **Route A / route B** | The model-assisted conversation, and the person's own terms used exactly as given. |
| **Accept** | The confirmation of the vocabulary: freezes the latest version; the first one wins. |
| **Change request** | Plain words asking the model for the next version, recorded with the version it was based on. |
| **Privacy receipt** | What one model call received of people's text: the masked text and its counts by type. |
| **Placeholder** | The token that replaces a masked value, such as `[PERSON_1]`; never a search term. |
| **Vocabulary gate** | The pause a run makes when its review has no accepted plan, until a member approves the draft. |
