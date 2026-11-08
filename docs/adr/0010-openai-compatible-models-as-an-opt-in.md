# ADR-0010: OpenAI-compatible model servers, including hosted APIs, as an operator opt-in

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Every model call goes through `InferenceProvider` ([`architecture.md` §3.1](../architecture.md#31-inferenceprovider)), and until now its only implementation spoke Ollama's native API. The first end-to-end runs on a laptop CPU showed how slow that is: a search plan took over five minutes until reasoning was switched off, and screening 38 papers stalled after two, at about one to three minutes a paper. Testers and small organisations without a GPU need a faster option.

Most fast options share one wire format, OpenAI's Chat Completions. That covers hosted APIs (OpenAI, Azure OpenAI, Mistral, Groq, OpenRouter) and self-hosted servers (vLLM, llama.cpp). Sending text to a hosted API is exactly what "the one rule" in `CLAUDE.md` guards: data leaves the deployment only to a destination the operator configured, and only after masking.

## Decision

- A second implementation, `OpenAICompatibleInferenceProvider`, posts to `{base}/chat/completions` with `response_format` of type `json_schema`. Every answer is checked against the schema, exactly as Ollama's are.
- The setting `inference_api` chooses the format: `ollama` (the default) or `openai`.
- Choosing `openai` opens nothing by itself. A hosted host is still refused unless `allow_hosted_inference` is set, and compose does not pass that setting through: turning it on means editing `compose.yaml`.
- Masking and `LeakCheckPromptGuard` sit in front of every provider (`GuardedInference`), so a hosted API receives the same masked text a local model does. The API key is the existing `inference_token` secret. The fetcher attaches it for the one configured host; the provider never sees it.
- The provider's error text is never repeated in our errors or logs, because it can echo the request.

## Rejected

- **A provider per vendor (OpenAI, Mistral, Groq…):** the same wire format several times over. Anthropic's API differs and would be its own implementation if asked for.
- **Strict structured outputs (`strict: true`):** strict mode refuses schemas with optional properties or `minLength`, which ours use. The schema is still sent, and the answer is validated either way.
- **A hosted API as the default, or as a fallback when Ollama is slow:** a default is the opposite of an opt-in, and a silent fallback would send text somewhere the operator never chose.

## Consequences

- A deployment can use a GPU-backed service for speed. Its operator then takes on a third party's terms: data processing agreement, region, retention.
- **Masking is not anonymity.** Codes, rare numbers and very specific descriptions can still identify people. The UI's privacy receipts show exactly what each call sent.
- Each `RunManifest` names the model the server reports, so runs remain traceable when the vendor's model changes.
- Proven by `tests/test_inference_openai.py`, `tests/test_settings.py::TestInferenceDestination` and `tests/test_outbound_wiring.py::TestInference`.
