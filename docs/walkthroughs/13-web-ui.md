# 13 — Web UI foundation

> **Milestones:** M13 (first pass) · **Status:** in progress · **Previous:** [05 — Accounts and sessions](05-accounts-and-sessions.md) · **Next:** `14-security-docs` (planned)

This phase gives humani-t a browser interface. A person can sign in with a password and a second factor (passkey, authenticator code or recovery code), set up that second factor on first sign-in and keep their recovery codes, see and create projects, open an empty project workspace with its composer, manage their signed-in devices and sign-in methods, re-authenticate in a dialog when the API asks for it, and switch between light and dark themes. It wears the LiGHT lab's brand, built on Chakra UI. Every rule stays in the API: the browser sends what was typed and follows the answer, as [`CLAUDE.md`](../../CLAUDE.md) requires. The app runs under a strict Content Security Policy with no `unsafe-inline`, styles allowed only with a nonce that Caddy makes fresh for every response, and Trusted Types enforced. It implements phase P1 of [`UI_UX_SPEC.md` §15](../UI_UX_SPEC.md#15-delivery-phases) up to the empty workspace, with §4 (layout), §6.1 (sign-in), §6.16 (account), §9 (design system), §11.2 and §11.4 (live events and errors), §12 (accessibility) and §13.1 (the JavaScript budget).

## What this phase delivers

- **Screens** (routes are code-based, typed by TanStack Router):

| Route | Screen | Who can reach it |
| --- | --- | --- |
| `/sign-in` | Password, then the second factor; resumes at the second factor after a reload | Anyone; a signed-in visitor is sent on |
| `/enrol` | First sign-in: add a passkey or an authenticator app, then save recovery codes | A session that still needs a second factor |
| `/` | Home: recent projects, or "Start with a question" | Signed in at full strength |
| `/projects` | All projects, with "Show archived projects" | Signed in |
| `/projects/new` | Create a project (name only, the API's contract today) | Signed in |
| `/p/$projectId` | The empty workspace: conversation log and composer | Signed in; the API answers 404 to non-members |
| `/settings` | Account: devices, sign-in methods, recovery codes, theme | Signed in |

- **App shell:** a header with the two logo slots (humani-t left, LiGHT right), a rail (labelled navigation), a main landmark, a footer, a skip link, a session-ended banner, a drawer below 1024 px, keyboard shortcuts `[`, `g h`, `g p`, `/` and Esc.
- **Brand:** LiGHT's colours, type and shapes as a Chakra UI system, with a derived dark theme, every colour pair measured ([`UI_UX_SPEC.md` §9](../UI_UX_SPEC.md#9-visual-design-system)), and named slots for the official logos and the licensed headline font ([`apps/web/public/brand/README.md`](../../apps/web/public/brand/README.md)).
- **Re-authentication:** any 401 with `reauthentication_required` opens a modal "Sign in again" dialog that says why, then retries the action once.
- **Live run events:** a hook that follows `GET /api/v1/runs/{run_id}/events`, applies each event once by its id, reopens the stream when the browser gives up, closes it after `job.finished`, and redraws progress at most twice a second. A one-line run strip shows the result. Neither is mounted on a screen yet: no screen starts a run in P1 so far.
- **API types:** `services/api/openapi.json`, exported from `create_app()` with no server running, and `apps/web/src/api/schema.gen.ts`, generated from it.
- **Root scripts:** `pnpm lint`, `pnpm format`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm gen:api`, `pnpm check:api`.
- **Pre-commit:** a `tsc` hook running `pnpm typecheck`.

## Architecture choices and why

### D13.1 Strict TypeScript, one compiler for the repository

- **Decision:** [`tsconfig.base.json`](../../tsconfig.base.json) sets `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noPropertyAccessFromIndexSignature`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `isolatedModules` and `noEmit`. `apps/web` has three projects that extend it: `tsconfig.app.json` (application code, no test types), `tsconfig.test.json` (tests, with Node types) and `tsconfig.node.json` (Vite, Vitest and the scripts). The root [`tsconfig.json`](../../tsconfig.json) references them, so `pnpm typecheck` is `tsc -b` from the root. TypeScript 6.0.3 is pinned once, in the pnpm catalog in [`pnpm-workspace.yaml`](../../pnpm-workspace.yaml).
- **Why:** the plan's M13 rule that type checking is a gate, like pyright. Separate projects mean application code cannot import test helpers, and tooling files are checked too.
- **Rejected:** TypeScript 7 (the native compiler). It is faster, but it ships no compiler API, and openapi-typescript needs one. One version for the whole repository was more important than speed.
- **Costs:** `noPropertyAccessFromIndexSignature` forces `record["key"]` on index signatures, which contradicts Biome's `useLiteralKeys`; that Biome rule is off. Test files are checked twice (they include application code), which costs a few seconds.
- **Where:** [`tsconfig.base.json`](../../tsconfig.base.json), [`apps/web/tsconfig.*.json`](../../apps/web/), [`biome.json`](../../biome.json).
- **Proven by:** T13.13 (a deliberate `any`, `!` and unchecked index are rejected).

### D13.2 API types are generated from the code, and checked for staleness

- **Decision:** [`scripts/export_openapi.py`](../../scripts/export_openapi.py) imports `create_app()` and writes `app.openapi()` to `services/api/openapi.json`. It clears every `HUMANI_T_*` variable first and uses the `test` environment, so the document depends on the code alone. [`apps/web/scripts/api-types.ts`](../../apps/web/scripts/api-types.ts) runs it, then openapi-typescript writes `schema.gen.ts`. `pnpm check:api` does both into a temporary folder and fails if either committed file differs.
- **Why:** [`CLAUDE.md`](../../CLAUDE.md): "API types are generated, never hand-written … a backend contract change should break the frontend type check, not its runtime".
- **Rejected:** fetching `/openapi.json` from a running server (needs a server, and `/openapi.json` is off in production); an `app.cli` command (it is a developer build step, not an operator command, and `cli.py` belongs to another lane).
- **Costs:** `check:api` needs `uv` and the API's Python environment, so the CI job that runs it needs both. The export uses the `test` environment, so the dev-only `GET /info` is in the document; the SPA does not call it.
- **Where:** the two scripts above; the API's `JsonValue` is mapped to `unknown` during generation, because openapi-typescript writes it as a self-referencing alias that `tsc` rejects (TS2502).
- **Proven by:** T13.14 (renaming a field in the schema fails `pnpm typecheck` in four screens); `pnpm check:api` itself.

### D13.3 One API client: same origin, JSON only, CSRF on every write

- **Decision:** [`src/api/client.ts`](../../apps/web/src/api/client.ts) wraps openapi-fetch. Its base URL is the page's own origin. A middleware reads the `__Host-csrf` cookie on every unsafe request and sends it as `X-CSRF-Token`; it reads the cookie each time, because the API rotates the token at sign-in and at step-up. A second middleware refuses to send a body that is not JSON, checking the body itself rather than only its header. `unwrap()` turns a failure into an `ApiError` built from the RFC 9457 problem document.
- **Why:** the API's three CSRF checks ([`architecture.md` §5.3](../architecture.md)): Origin/Fetch-Metadata, the double-submit token, and JSON-only bodies.
- **Rejected:** a hand-written `fetch` wrapper (no types); axios (a second HTTP stack, and its own cookie and header defaults to audit).
- **Proven by:** `src/api/client.test.ts`: the token is sent on POST and DELETE, a rotated token is used at once, reads send none, a form body is refused.

### D13.4 Errors by kind, in the API's words

- **Decision:** [`src/api/problem.ts`](../../apps/web/src/api/problem.ts) classifies each failure as one of the §11.4 cases (`invalid`, `signed-out`, `reauthentication-required`, `mfa-required`, `mfa-enrolment-required`, `forbidden`, `not-found`, `conflict`, `throttled`, `server`, `offline`). The message shown is the API's `detail`, except where the spec fixes the words: 404 always reads "This project doesn't exist or you don't have access." (so a missing project and a forbidden one look the same), a wrong password reads "That email and password don't match.", and a server error never shows raw text, only the request id.
- **Retries** ([`src/app/queryClient.ts`](../../apps/web/src/app/queryClient.ts)): a dropped connection is retried twice; a 429 or 503 with `Retry-After` once, at that time; every other 4xx is an answer, shown at once.
- **Proven by:** `src/api/problem.test.ts`.

### D13.5 The session ending under the page

- **Decision:** [`src/app/App.tsx`](../../apps/web/src/app/App.tsx) watches every query and mutation. A plain 401 while a session was cached means the session ended (idle timeout, revoked elsewhere): it shows the banner "You've been signed out. Sign in again to continue." with a link back, forgets the cached session, and leaves the page as it was. Unsent composer text lives in page memory ([`src/app/memory.tsx`](../../apps/web/src/app/memory.tsx)), never in storage, and survives the trip through sign-in.
- **Landmine:** the re-authentication request is a plain request, not a TanStack mutation. A wrong password there answers 401, which the watcher would read as "signed out".
- **Proven by:** `src/shell/AppShell.test.tsx` ("says so when the session has ended"), `src/features/projects/WorkspacePage.test.tsx` ("keeps unsent text in memory … `localStorage` stays empty").

### D13.6 Route guards only decide where to go

- **Decision:** [`src/app/router.tsx`](../../apps/web/src/app/router.tsx) checks `GET /auth/session` before each signed-in route and sends the browser to `/sign-in` (signed out), `/sign-in?step=second-factor` (pending second factor) or `/enrol` (no second factor yet). The API still answers 401, 403 or 404 whatever the browser believed.
- **The `redirect` parameter** is checked by [`safeRedirect`](../../apps/web/src/features/auth/redirect.ts): a path on this origin, or nothing. `//evil.example` is refused (an open redirect).
- **Landmine:** TanStack Router lays each route's validated search over its parent's *raw* search. A `validateSearch` that leaves a rejected key out lets the unchecked value through from the URL. The sign-in route therefore always returns both keys (as `undefined` when rejected), and the redirect is checked again where it is used. The test for this failed before the fix.
- **Proven by:** `SignInPage.test.tsx` ("never follows a redirect to another site").

### D13.7 Passkeys: the browser's ceremony, the API's decision

- **Decision:** [`src/features/auth/webauthn.ts`](../../apps/web/src/features/auth/webauthn.ts) checks the options the API sends (they arrive as a free-form object), converts base64url to bytes for `navigator.credentials`, and converts the credential back to the JSON py_webauthn verifies. The passkey prompt starts by itself at sign-in, once, even under React's double effects in development.
- **Rejected:** `PublicKeyCredential.parseCreationOptionsFromJSON` and `toJSON()`. They are simpler, but reached every major browser only in 2025, and field teams keep older devices. One tested path works on both.
- **Proven by:** `webauthn.test.ts`, and the passkey cases in `SignInPage.test.tsx`, `EnrolPage.test.tsx` and `AccountPage.test.tsx`, against a software authenticator ([`src/test/fakeAuthenticator.ts`](../../apps/web/src/test/fakeAuthenticator.ts)).

### D13.8 Re-authentication in a dialog, then one retry

- **Decision:** [`ReauthDialog.tsx`](../../apps/web/src/features/auth/ReauthDialog.tsx) provides `withReauth(reason, action)`. It runs the action; on a 401 with `reauthentication_required` it opens a modal over the current screen that says why ("Removing a passkey needs you to sign in again."), and on success runs the action once more. Cancelling throws `ReauthCancelledError`, which screens treat as "the person changed their mind", not as a failure. The dialog is Chakra UI's `Dialog` ([`ui/Dialog.tsx`](../../apps/web/src/ui/Dialog.tsx)): it traps focus, hides the page behind from assistive technology, closes on Esc, and returns focus to the button that opened it. It replaced a native `<dialog>` when the screens moved to Chakra (D13.13); the native one needed no script, but would have been the only component styled outside the system.
- **Why:** UI_UX_SPEC §6.1. The browser never decides which actions are sensitive; the API says so.
- **Proven by:** the "re-authentication" tests in `AccountPage.test.tsx` (exactly two DELETE requests: the refused one and the retry).

### D13.9 Live run events: resume, apply once, throttle

- **Decision:** [`useRunEvents`](../../apps/web/src/features/runs/useRunEvents.ts) opens one `EventSource`. When the browser retries by itself, it sends `Last-Event-ID` and the API replays what was missed; the hook only shows "Reconnecting…". When the browser gives up (`readyState` CLOSED), the hook opens a new stream after 1, 2, 4 … 30 seconds. That stream carries no `Last-Event-ID`, so the API replays from the start. [`applyRunEvent`](../../apps/web/src/features/runs/runEvents.ts) ignores any event whose id is not above the last one applied: ids increase by one within a run, so that test is exact. After `job.finished` the hook closes the stream itself; otherwise the server's close would look like a drop and the browser would replay the finished run forever. `stage.progress` redraws at most every 500 ms, and every other event redraws at once.
- **Hand-written contract:** the event payloads are not in the OpenAPI document (an SSE body has no schema there), so `runEvents.ts` defines them and checks every field at runtime. They were checked against `services/api/app/domain/runs.py`, which showed `stage.progress.item` to be a string.
- **Proven by:** `runEvents.test.ts`, `useRunEvents.test.tsx` (against [`fakeEventSource.ts`](../../apps/web/src/test/fakeEventSource.ts)), `RunStrip.test.tsx`.

### D13.10 Brand tokens in a Chakra UI system, never in components

- **Decision:** the LiGHT brand is a Chakra UI system, [`src/theme/system.ts`](../../apps/web/src/theme/system.ts): the seven brand colours as raw tokens, and semantic tokens (`bg`, `fg.muted`, `border.emphasized`, `danger.*`, `caveat.*`, `benefit.*`, `risk.*`, `masked.*`, `focusRing`) that components read and that switch with the theme. Only the recipes the app uses are registered. Buttons are pills and inputs have 8 px corners, after the lab's site and guidelines. The theme is a `dark` or `light` class on `<html>`, set before the first render from the person's choice (`data-theme`) or the system's, and updated when the system's changes. No component names a hex value except the QR code. `src/csp.test.ts` still fails on any `style=` prop, `dangerouslySetInnerHTML`, HTML string sink, `eval`, DOM style write or cross-origin URL.
- **Why:** one place to change the brand, and accessibility decided once: every pair the tokens form is measured in [`UI_UX_SPEC.md` §9.2.2](../UI_UX_SPEC.md#922-measured-contrast). Light Blue and both yellows fail as text on Eggshell, so on light backgrounds they are decoration only; control edges use `border.emphasized` (3.7:1), never the hairline.
- **Rejected:** keeping the CSS files and restyling them to the brand. It would have left the lab's recommendation (a component library) unmet, and class names are checked by nothing; Chakra's style props are typed.
- **Costs:** a Chakra component whose recipe is not registered renders unstyled. Add its recipe to `system.ts` when you first use one. Chakra's own `Field` was not used: it discovers its hint and error after rendering, so the accessible description would be wrong on the first paint; [`ui/Field.tsx`](../../apps/web/src/ui/Field.tsx) wires the ids itself.
- **Landmine:** `preflight: true` in the system is what gives `box-sizing: border-box` and makes buttons and inputs use Manrope. It comes with Chakra's full theme, which this system does not use, so it is set explicitly. Without it inputs overflow their cards.

### D13.11 The strict CSP, with a nonce for styles

- **Decision:** the policy is built in one place, [`apps/web/csp.config.ts`](../../apps/web/csp.config.ts), and Caddy's copy ([`infrastructure/caddy/Caddyfile`](../../infrastructure/caddy/Caddyfile)) must equal it: `default-src 'none'`, `script-src 'self'`, `style-src 'self' 'nonce-…'`, `require-trusted-types-for 'script'`, `trusted-types 'none'`, `frame-ancestors 'none'`, `upgrade-insecure-requests`, …. Chakra UI's styles are written at run time by Emotion, so they need a nonce ([ADR 0004](../adr/0004-chakra-ui-with-a-nonce-csp.md)):
  - **Build:** Vite's `html.cspNonce` is set to the Go template ``{{placeholder `http.request.uuid`}}``. Vite puts it on every script and stylesheet tag and in `<meta property="csp-nonce">`.
  - **Caddy:** `templates @page` fills in the template, only for `/` and `/index.html` (the matcher runs after `try_files`, so every app route qualifies). The header uses `{http.request.uuid}`, the same request's UUIDv4 (122 random bits), so page and header always agree and change on every response. `index.html` is `no-store`, because a stored copy would carry an old nonce under a new header. `/assets/*` is never templated and keeps its immutable caching.
  - **Browser:** [`BrandProvider`](../../apps/web/src/theme/BrandProvider.tsx) reads the nonce from the meta tag and gives Emotion a cache created with it (`createCache({ key: "css", nonce })` in `CacheProvider`), which is how Chakra v3 takes a nonce; every `<style>` Emotion adds carries it.
  - **`vite preview`:** a plugin in `csp.config.ts` does what Caddy does: a fresh `randomUUID()` per page, put in the page and the header.
  - **Dev server:** sends a relaxed policy, `devContentSecurityPolicy()`. It relaxes exactly four things: the nonce is fixed (`humani-t-dev`) instead of per response; `script-src` also allows that nonce, for React Refresh's one inline script; `connect-src` adds `ws://localhost:<port>` for hot reload; and Trusted Types is sent as `Content-Security-Policy-Report-Only`, because Vite's error overlay writes `innerHTML`. `upgrade-insecure-requests` is left out; it has no effect on plain-http localhost.
- **Trusted Types:** Chakra, Ark UI, Emotion and the zag.js machines the app uses write no HTML strings (checked by searching their published code; only zag's number input, which the app does not use, touches `innerHTML`). Emotion inserts rules with `insertRule` on a nonce'd `<style>`, which Trusted Types does not cover. Under `require-trusted-types-for 'script'` the sign-in page renders with no violation in Edge, through `vite preview` and through Caddy, and the dev server reports none. Trusted Types stays enforced.
- **Note:** `upgrade-insecure-requests` does not break the plain-http preview because it is served on `localhost`, which browsers treat as secure (a preview opened on a LAN address with `--host` would have its assets upgraded to https and fail). Assets are never inlined as `data:` URIs. Fonts are self-hosted. The bundle contains React DOM's `innerHTML` path for `dangerouslySetInnerHTML`, which the app never uses; under `trusted-types 'none'` it would throw rather than run.
- **Proven by:** `src/csp.test.ts` (the source scans; *the policy* › *is exactly what Caddy sends*; *the nonce Caddy puts in the page* › placeholder, scope, `no-store`, substitution); [`.github/scripts/check-security-headers.sh`](../../.github/scripts/check-security-headers.sh) in the compose smoke test (*the page's style nonce*: page and header match, differ per response, assets untouched); T13.12 and T13.19–T13.20.

### D13.12 Tests: the whole app against a fake API

- **Decision:** most tests render the whole app (router, queries, providers) at a URL and drive it as a person would, with Testing Library and user-event. The network is MSW, serving [`src/test/fakeApi.ts`](../../apps/web/src/test/fakeApi.ts): a stateful fake that checks passwords, requires the second factor, rejects writes without the CSRF token from the cookie, and refuses sensitive actions after a stale sign-in. Each screen test ends with an axe check ([`src/test/axe.ts`](../../apps/web/src/test/axe.ts)), minus colour contrast, which jsdom cannot compute.
- **Why:** [`CLAUDE.md`](../../CLAUDE.md), fakes over mocks. A fake that returned canned answers would let every screen pass whether or not it sent the right request.
- **Costs:** about 60 s for the suite on Windows; timeouts are raised (`asyncUtilTimeout` 15 s, `testTimeout` 30 s), because a file's first cold render took 9 s on a loaded machine. jsdom has no `<dialog>` modality, `EventSource` or WebAuthn; the setup adds the smallest stand-in for each, and the real behaviour is in the manual tests.

## Libraries

### React 19.3.1 and React DOM

- **What it does:** the component model and the DOM renderer.
- **Why this one:** the plan's choice; React escapes all text by default, which keeps API strings out of the HTML parser.
- **Trade-offs:** about 62 KB gzipped of the 206 KB first-load JavaScript.
- **What we gave up:** server rendering; the SPA is static files behind Caddy.

### TanStack Router 1.170.46

- **What it does:** routes, guards (`beforeLoad`), search-parameter validation, links.
- **Why this one:** routes are typed end to end. `<Link to="/p/$projectId" params={…}>` fails `pnpm typecheck` if the route or a parameter is wrong, the same promise the generated API types make. Code-based routes need no code generator.
- **Alternatives:** React Router 8 (smaller API, but typed paths only in framework mode with its own build plugin).
- **Trade-offs:** a larger API to learn; the search-merging behaviour in D13.6 is a real footgun.

### TanStack Query 5.105.1

- **What it does:** caches server state, refetches, retries, tracks loading and error for every read and write.
- **Why this one:** query keys give reliable invalidation (create a project, and every list refetches), and its cache events give one place to notice a 401 (D13.5).
- **Alternatives:** SWR (no mutation model); hand-written `useEffect` fetching (races, no cache).
- **Trade-offs:** about 13 KB gzipped.

### openapi-fetch 0.17.0 and openapi-typescript 7.13.0

- **What they do:** openapi-typescript turns `openapi.json` into types; openapi-fetch is a 6 KB `fetch` wrapper typed by them, with middleware.
- **Why these:** the pair the plan names, and the smallest way to make every path, parameter, body and answer checked by `tsc`.
- **Trade-offs:** openapi-typescript declares TypeScript `^5`; it runs on 6.0.3 (allowed in `pnpm-workspace.yaml`), and `pnpm check:api` exercises it on every CI build.

### uqr 0.1.3

- **What it does:** turns the `otpauth://` URI into a QR matrix.
- **Why this one:** no dependencies, MIT. It returns a boolean matrix, which the app draws as one SVG path: no image, no `data:` URI, no `innerHTML`.
- **What we gave up:** nothing needed; typing the key by hand is still offered.

### Chakra UI 3.37.0, with @emotion/react and @emotion/cache 11.14.0

- **What it does:** layout primitives, typed style props, and accessible components (dialog, checkbox, radio group, table, alert) built on Ark UI and zag.js state machines; Emotion writes the styles.
- **Why this one:** the lab's recommendation for its projects, so humani-t looks and behaves like its siblings. Its components carry the keyboard and ARIA behaviour the spec asks for, and its tokens make the brand one file.
- **Alternatives:** Ark UI with plain CSS (the same behaviour without the runtime styling, so no nonce; rejected in ADR 0004 because the lab asked for Chakra and every style would have been hand-written again); keeping the hand-written CSS.
- **Trade-offs:** about 72 KB gzipped (Chakra, Ark, zag, Emotion), which takes the first load to 206 KB against the spec's 170 KB budget (G13.11). Styles are written at run time, so the CSP needs a per-response nonce (D13.11).

### lucide-react 1.66.0

- **What it does:** Lucide's icons as React components, one import per icon, tree-shaken.
- **Why this one:** the spec's icon set, already used through a hand-copied subset; the package removes the copying. Icons are `aria-hidden` unless given a name, as before.

### @fontsource-variable/manrope 5.3.0 and @fontsource-variable/playfair-display 5.3.0

- **What they do:** Manrope (body) and Playfair Display (headings until the licensed Ivy Presto Headline arrives) as self-hosted variable woff2 files, both SIL Open Font Licence.
- **Why these:** LiGHT's guidelines set Manrope for text and Ivy Presto Headline for titles. Ivy Presto is licensed through Adobe and cannot be fetched from Adobe's servers under the CSP or the one rule; Playfair is the closest open high-contrast serif. Headings use the family name `"Brand Headline"` ([`src/theme/fonts.css`](../../apps/web/src/theme/fonts.css)), so the licensed files replace it in one file.
- **Trade-offs:** `unicode-range` means only the Latin files load for English text (about 25 KB for Manrope, 50 KB for the headline font); the other subsets are in `dist/` but never downloaded.

### Vite 8.3.5 and @vitejs/plugin-react 6.2.1

- **What they do:** the dev server with its `/api` proxy, the production build, `vite preview` with the CSP.
- **Why:** the plan's choice; the output is plain static files with no inline script.

### Vitest 5.0.5, jsdom 30.1.3, Testing Library (react 16.3.3, dom 10.4.2, user-event 14.6.8, jest-dom 7.0.1)

- **What they do:** run tests in a simulated browser and query it the way a person or a screen reader would (by role and accessible name).
- **Why:** shares Vite's config and transforms; role-based queries double as accessibility checks.

### MSW 3.0.3

- **What it does:** intercepts `fetch` at the network level, so the app's real client, middleware and error handling all run.
- **Footgun:** MSW 3 renamed `onUnhandledRequest` to `onUnhandledFrame`; the old name was ignored silently until `pnpm typecheck` caught it.

### axe-core 4.14.0

- **What it does:** the WCAG rule engine behind most accessibility checkers.
- **Why:** UI_UX_SPEC §12 asks for axe in the test suite. Used directly, without a wrapper package.

## Files to read, in order

Start at the edge the browser talks through, then the frame, then the screens.

1. [`apps/web/vite.config.ts`](../../apps/web/vite.config.ts) and [`csp.config.ts`](../../apps/web/csp.config.ts): the dev proxy, the CSP and its nonce for each server, why assets are never inlined.
2. [`apps/web/src/theme/system.ts`](../../apps/web/src/theme/system.ts), [`BrandProvider.tsx`](../../apps/web/src/theme/BrandProvider.tsx) and [`fonts.css`](../../apps/web/src/theme/fonts.css): the brand as tokens, the nonce'd style cache, the headline font slot.
3. [`apps/web/src/api/client.ts`](../../apps/web/src/api/client.ts): CSRF and JSON-only middleware, `unwrap`.
4. [`apps/web/src/api/problem.ts`](../../apps/web/src/api/problem.ts): failure kinds and the words shown.
5. [`apps/web/src/app/router.tsx`](../../apps/web/src/app/router.tsx): the site map, the guards, the `validateSearch` landmine.
6. [`apps/web/src/app/App.tsx`](../../apps/web/src/app/App.tsx) and [`memory.tsx`](../../apps/web/src/app/memory.tsx): providers, the session-end watcher, drafts in memory.
7. [`apps/web/src/shell/AppShell.tsx`](../../apps/web/src/shell/AppShell.tsx), [`Rail.tsx`](../../apps/web/src/shell/Rail.tsx), [`useShortcuts.ts`](../../apps/web/src/shell/useShortcuts.ts).
8. [`apps/web/src/features/auth/SignInPage.tsx`](../../apps/web/src/features/auth/SignInPage.tsx), [`SecondFactor.tsx`](../../apps/web/src/features/auth/SecondFactor.tsx), [`signInOutcome.ts`](../../apps/web/src/features/auth/signInOutcome.ts), [`webauthn.ts`](../../apps/web/src/features/auth/webauthn.ts).
9. [`EnrolPage.tsx`](../../apps/web/src/features/auth/EnrolPage.tsx), [`TotpSetup.tsx`](../../apps/web/src/features/auth/TotpSetup.tsx), [`RecoveryCodes.tsx`](../../apps/web/src/features/auth/RecoveryCodes.tsx), [`ReauthDialog.tsx`](../../apps/web/src/features/auth/ReauthDialog.tsx).
10. [`apps/web/src/features/projects/`](../../apps/web/src/features/projects/) and [`features/account/AccountPage.tsx`](../../apps/web/src/features/account/AccountPage.tsx).
11. [`apps/web/src/features/runs/runEvents.ts`](../../apps/web/src/features/runs/runEvents.ts), then [`useRunEvents.ts`](../../apps/web/src/features/runs/useRunEvents.ts).
12. [`apps/web/src/test/fakeApi.ts`](../../apps/web/src/test/fakeApi.ts), then any `*.test.tsx`: the tests are the specification.

## Set up

Do [00 — Setup](00-setup.md) first: Postgres, Valkey and Mailpit running, migrations applied, an administrator created. Then:

```powershell
pnpm install
```

**The public origin in development.** The browser talks only to Vite (`http://localhost:5173`), which forwards `/api` to the API on `127.0.0.1:8731`. The browser therefore sends `Origin: http://localhost:5173`, and the API refuses any write whose origin is not `HUMANI_T_PUBLIC_ORIGIN` ("Cross-site requests are not allowed."). Passkeys use the same setting: the relying party id is its host (`localhost`) and the API expects ceremonies from that exact origin. In `services\api\.env`, change the line to:

```text
HUMANI_T_PUBLIC_ORIGIN=http://localhost:5173
```

and change it back to `https://localhost` when you use the Caddy stack. Three things follow:

- Open **`http://localhost:5173`**, not `http://127.0.0.1:5173`. The origin must match exactly, and a passkey's relying party must be a domain name, never an IP address.
- Use **Chrome, Edge or Firefox**. They treat `http://localhost` as a secure context, so they accept the API's `Secure`, `__Host-` cookies over plain HTTP there. Safari does not, and sign-in will look as if it did nothing.
- Passkeys made in development belong to `localhost`. They also work under `https://localhost` (the relying party id ignores scheme and port), and nowhere else.

Start the API and the web app in two PowerShell windows:

```powershell
cd services\api
uv run --env-file .env uvicorn app.main:app --reload --port 8731 --no-proxy-headers
```

```powershell
pnpm --filter @humani-t/web dev
```

To check a production build under the strict CSP:

```powershell
pnpm build; if ($?) { pnpm --filter @humani-t/web preview }
```

Then open `http://localhost:4173`. The preview server also proxies `/api`, but its origin is `:4173`, so set `HUMANI_T_PUBLIC_ORIGIN=http://localhost:4173` for that session. It sends the production policy with a fresh nonce on every page, as Caddy does.

To check Caddy's nonce handling without the whole stack (T13.19), serve the build with the pinned Caddy image on a spare port. Pages load; `/api` answers 502, because there is no API behind it:

```powershell
docker run -d --name humani-t-smoke-caddy -p 127.0.0.1:58088:8080 -e HUMANI_T_DOMAIN=http://:8080 -v "${PWD}\apps\web\dist:/srv:ro" -v "${PWD}\infrastructure\caddy\Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.7-alpine
curl.exe -s -D - -o NUL http://127.0.0.1:58088/
docker rm -f humani-t-smoke-caddy
```

## Manual tests

Unless a test says otherwise, use Chrome or Edge at `http://localhost:5173`, with DevTools open on the Console and Network tabs. "The administrator" is the account from `create-admin`.

| # | Do | Expect | Proves | If it fails |
| --- | --- | --- | --- | --- |
| T13.1 | Open `http://localhost:5173/projects` in a fresh private window. | The URL becomes `/sign-in?redirect=%2Fprojects`; the page says "Sign in". | D13.6 | The guard in `router.tsx`; the API answer to `GET /api/v1/auth/session` (expect 401). |
| T13.2 | Sign in with the administrator's email and a wrong password. | "That email and password don't match." No hint whether the account exists. | D13.4, enumeration parity | `SignInPage.tsx`; the API answer should be 401. |
| T13.3 | Submit a wrong password repeatedly until the API throttles (see 05's limits). | "Too many attempts. Try again in N seconds." counting down; Sign in disabled until 0. | §6.1 countdown | Network tab: the 429 needs a `Retry-After` header. |
| T13.4 | Sign in correctly for the first time (no second factor yet). | `/enrol`, "Protect your account". `GET /api/v1/projects` in another tab answers 403 with `mfa_enrolment_required`. | D13.6, M9 enforcement | `enrolRoute` guard. |
| T13.5 | Choose "Add a passkey" and complete the browser prompt (Windows Hello or a security key). | "Save your recovery codes" with 10 codes. Continue stays disabled until "I've saved my recovery codes" is ticked. Download gives `humani-t-recovery-codes.txt`. | D13.7, §6.1 | Console: a `SecurityError` means the origin and `HUMANI_T_PUBLIC_ORIGIN` differ (see Set up). |
| T13.6 | Sign out (rail), sign in again with the password. | The passkey prompt appears by itself; using it lands on Home. Press Cancel instead: "The passkey wasn't used." with "Try the passkey again". | D13.7 | `SecondFactor.tsx`. |
| T13.7 | At the second factor, choose "Use a recovery code" and enter one of the saved codes in lower case without dashes. | Signed in. On Account, "9 recovery codes left". | M9 recovery codes | API: codes are case- and dash-insensitive. |
| T13.8 | At the second factor, reload the page. | Still "Confirm it's you", with every method offered. | D13.6 | `toSignIn(…, "second-factor")`. |
| T13.9 | On Home with no projects: follow "New project", create "Cash vs vouchers, Gao". Then create a project named with only spaces. | The workspace opens, titled with the name. The second shows the API's reason under the field, and the field is marked invalid. | D13.3, D13.4 | Network: the POST must carry `X-CSRF-Token` and `Content-Type: application/json`. |
| T13.10 | In the workspace, type in the composer, go Home with `g` then `h`, and come back via the rail. | The text is still there. DevTools → Application → Local Storage holds no question text. | D13.5 | `memory.tsx`. |
| T13.11 | On Account, choose "Remove" on a passkey more than 15 minutes after signing in. | A modal "Sign in again" says "Removing a passkey needs you to sign in again."; Tab cycles inside it; Esc closes it and nothing is removed. Enter the password: the passkey is removed (or, if it is the last method, the API's message explains why not). | D13.8 | Network: one 401 with `reauthentication_required`, then `POST /auth/reauthenticate` 204, then the retried DELETE. |
| T13.12 | Run the preview under the CSP (Set up), sign in and visit every screen, opening the re-authentication dialog once (T13.11). | No "Refused to …" or Trusted Types errors in the Console. Response headers of `/` include the CSP with a `'nonce-…'` in `style-src`. | D13.11 | A violation names the blocked resource; fix the code, never the policy. |
| T13.13 | Add `const x: any = 1;` and `const s: string = ["a"][0];` to any file in `apps/web/src`. Run `pnpm lint` and `pnpm typecheck`, then delete them. | Biome: `noExplicitAny`. tsc: `Type 'string \| undefined' is not assignable to type 'string'`. | D13.1 | `biome.json`, `tsconfig.base.json`. |
| T13.14 | After any change to an API route or schema, run `pnpm check:api`, then `pnpm gen:api`, then `pnpm typecheck`. | Before `pnpm gen:api`: "Stale, run `pnpm gen:api` and commit". After: "up to date", and `pnpm typecheck` shows every screen the change affects. | D13.2 | `uv` must be on the PATH. |
| T13.15 | Resize to 1100 px, then 800 px, then 375 px wide. Zoom to 200%. | 1100: rail as icons, each still named on hover and to a screen reader. 800 and 375: a menu button opens the rail as a drawer; Esc or the scrim closes it. No sideways scroll. | §4.2, §12 | The media queries in `shell/railStyles.ts`. |
| T13.16 | Use the theme button in the rail: System → Light → Dark. Reload. | The choice is kept; the page does not flash light before turning dark. | §9.2 | `preferences.ts`, `main.tsx`. |
| T13.17 | Sign in in two browsers. In one, Account → "Sign out all other devices". In the other, click "Show archived projects" on All projects. | The second shows "You've been signed out. Sign in again to continue." and keeps the page; "Sign in again" returns to `/projects` after signing in. | D13.5 | `App.tsx` watcher. |
| T13.18 | With NVDA (Windows) or VoiceOver, sign in and open Home. | The skip link is the first Tab stop; the rail is announced as "Main, navigation"; each page's heading is announced after a navigation. | §12 | `PageHeading.tsx`. |
| T13.19 | Build, then serve through Caddy (the compose stack, or the image alone: see below). In DevTools → Network, open the document request for `/` twice, and once for `/projects`. | Each response has a different `nonce-…` in `Content-Security-Policy`, and the same value appears in the page's `<meta property="csp-nonce">` and on its `<script>` and `<link>` tags (View source; the Elements panel hides nonces). `Cache-Control: no-store`. An `/assets/*.js` response has `Cache-Control: … immutable`, an `ETag`, and no `http.request.uuid` in its body. | D13.11 | A literal ``{{placeholder …}}`` in the page means `templates` did not run: check the `@page` matcher. A nonce in the page but styles refused: the header and the page came from different requests (a cache). |
| T13.20 | Run `pnpm --filter @humani-t/web dev` and open `http://localhost:5173` with the Console open. Edit a component and save. | The page loads styled; hot reload applies the edit; no "Refused to …" errors. Response headers carry the dev policy (fixed nonce `humani-t-dev`) and a `Content-Security-Policy-Report-Only` for Trusted Types. | D13.11 | The dev policy in `csp.config.ts`. |
| T13.21 | Open `/sign-in`, then Home, All projects and Account, in the light theme and then the dark one. Compare with [light-laboratory.org](https://www.light-laboratory.org/). | Eggshell page; titles in the headline serif; text in Manrope; pill-shaped buttons; 8 px cards with hairlines; section labels in small capitals with a black dot and a rule; humani-t on the left and "by LiGHT" on the right in the header, on the cover and in the footer; the light-beam only on the sign-in cover. Dark theme: blue-black surfaces, Eggshell text, Light Blue buttons and links. | §9 | `theme/system.ts`, `UI_UX_SPEC.md` §9.9. |
| T13.22 | With the browser's DevTools → Rendering → "Emulate vision deficiencies" (achromatopsia), look at an error, the signed-out banner (T13.17) and a waiting run strip. | Each still reads by its words and icon alone. | §9.2.3, WCAG 1.4.1 | The component's icon or word is missing. |

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T13.1 | ☐ | |
| T13.2 | ☐ | |
| T13.3 | ☐ | |
| T13.4 | ☐ | |
| T13.5 | ☐ | |
| T13.6 | ☐ | |
| T13.7 | ☐ | |
| T13.8 | ☐ | |
| T13.9 | ☐ | |
| T13.10 | ☐ | |
| T13.11 | ☐ | |
| T13.12 | ☐ | |
| T13.13 | ☐ | |
| T13.14 | ☐ | |
| T13.15 | ☐ | |
| T13.16 | ☐ | |
| T13.17 | ☐ | |
| T13.18 | ☐ | |
| T13.19 | ☐ | |
| T13.20 | ☐ | |
| T13.21 | ☐ | |
| T13.22 | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the request, the response and the API log line (with its request id). It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| CSRF header from the cookie on every write, never on reads; rotated tokens used at once; JSON bodies only | `apps/web/src/api/client.test.ts` |
| Every §11.4 status maps to the right kind; 404 wording is the same for missing and forbidden | `apps/web/src/api/problem.test.ts` |
| Sign-in: one message for every wrong password, countdown on 429, passkey/TOTP/recovery code, resume after reload, no off-site redirect | `apps/web/src/features/auth/SignInPage.test.tsx` |
| First sign-in: passkey or app, codes shown once, explicit confirmation | `apps/web/src/features/auth/EnrolPage.test.tsx` |
| Passkey options and credentials converted as WebAuthn JSON; malformed options refused before the prompt | `apps/web/src/features/auth/webauthn.test.ts` |
| Sign-in answers narrowed; unknown methods dropped | `apps/web/src/features/auth/signInOutcome.test.ts` |
| Shell landmarks, skip link, shortcuts off while typing, drawer, sign-out, session-ended banner, themes | `apps/web/src/shell/AppShell.test.tsx` |
| Home, all projects, create project, workspace, drafts in memory | `apps/web/src/features/projects/*.test.tsx` |
| Composer: Enter, Shift+Enter, growth with wrapped lines, kept text when sending is unavailable | `apps/web/src/features/chat/Composer.test.tsx` |
| Devices, sign-in methods, re-authentication and its single retry, theme | `apps/web/src/features/account/AccountPage.test.tsx` |
| Run events applied once, reconnect, closed after the run, progress throttled | `apps/web/src/features/runs/*.test.ts(x)` |
| Nothing a strict CSP would block; Caddy's policy equals ours; the page's nonce comes from the header's placeholder, only for the page, never cached | `apps/web/src/csp.test.ts` |
| Through the running stack: page and header nonces match, change per response, assets untouched | `.github/scripts/check-security-headers.sh` (compose smoke test) |
| No WCAG violations axe can detect, on every screen | the "has no accessibility violations" test in each screen's file |

How to run only this phase's tests:

```powershell
pnpm test
pnpm --filter @humani-t/web exec vitest run src/features/auth
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G13.1 | No Playwright smoke test against the Compose stack (plan M13). | Cookies, real WebAuthn and the real CSP are checked by hand only (T13.5, T13.12). | With the deploy lane's Caddy image. |
| G13.2 | Needs-you, pinned projects, collections, activity and search are not shown. | Home shows recent projects only. | P3, when the API serves them. |
| G13.3 | The workspace cannot start a review; the run strip and hook are not mounted. | The composer keeps text but cannot send it. | The planning and runs lanes; `POST /projects/{id}/reviews` and `/runs/{id}/events` now exist in the schema. |
| G13.4 | Password change, text size, reduced-motion and low-data preferences are missing from Account. | Password change only through the API. | Next pass. |
| G13.5 | A 429 at sign-in counts down but does not retry by itself at zero (§11.4). | The person presses Sign in again. | Deliberate: re-sending credentials unasked is surprising; to confirm with the owner. |
| G13.6 | When the browser gives up on a run stream, the new stream carries no `Last-Event-ID` (EventSource cannot set headers), so the API replays the whole run. | More bytes on a bad connection; no wrong state (events apply once). | Ask the API to also accept `?last_event_id=`. |
| G13.7 | Focus moves to a page's heading only when focus was on an element before the navigation. | After a keyboard shortcut navigation, focus stays at the top of the page. | Revisit with screen-reader testing (T13.18). |
| G13.8 | Opening the drawer does not move focus into it. | Keyboard users press Tab once more. | Small follow-up. |
| G13.9 | Canvas drag-resizing and the canvas itself are not built. | — | With the first canvas view. |
| G13.10 | Colour contrast is not checked automatically (jsdom). | A token change could fail WCAG 1.4.3 unnoticed. The measured table in `UI_UX_SPEC.md` §9.2.2 is the check until then. | Lighthouse in CI (§13.1), or the Playwright run. |
| G13.11 | First-load JavaScript is 206 KB gzipped, over the spec's 170 KB budget (§13.1); it was 125 KB before Chakra. | Slower first load on poor connections. | The owner decides: split routes, replace Ark-backed parts with native ones, move to Panda CSS, or raise the budget (see the report on the brand change). |
| G13.12 | The LiGHT and humani-t logos and the licensed Ivy Presto Headline files are not in the repository. | Text stands in for the logos; Playfair Display stands in for the headline font. | Drop them in as `apps/web/public/brand/README.md` says. |
| G13.13 | `index.html` is `no-store`, so it is fetched on every visit. | One small request more per visit; hashed assets are still cached for good. | None: a cached page would carry a stale nonce. |

## Glossary

| Term | Meaning |
| --- | --- |
| **SPA** | Single-page app: one HTML page whose JavaScript draws every screen and changes the URL without reloading. |
| **CSP** | Content Security Policy: a response header listing where a page may load scripts, styles, images and fonts from. A strict one stops injected scripts from running. |
| **Nonce** | A random value the server puts both in the CSP header and on the elements it allows; an injected `<style>` cannot know it. Made fresh for every response. |
| **Trusted Types** | A CSP feature that stops code from passing plain strings to HTML-parsing APIs such as `innerHTML`. |
| **CSRF token** | A per-session secret the page copies from a readable cookie into a header, proving a request came from our own page. |
| **Problem document** | The JSON body of every API error (RFC 9457): `title`, `status`, `detail`, plus extensions such as `reauthentication_required`. |
| **Passkey** | A WebAuthn credential: a key pair kept by the device or a security key, unlocked by fingerprint, face or PIN. It cannot be phished, because the browser binds it to the site's domain. |
| **Relying party (RP) id** | The domain a passkey belongs to; here, the host of `HUMANI_T_PUBLIC_ORIGIN`. |
| **TOTP** | Time-based one-time password: the 6-digit code an authenticator app shows, changing every 30 seconds. |
| **AAL** | Authenticator assurance level: 1 after a password, 2 after a second factor. |
| **SSE** | Server-Sent Events: a long HTTP response carrying a stream of named events, read by the browser's `EventSource`. |
| **`Last-Event-ID`** | The header `EventSource` sends when it reconnects, naming the last event it saw, so the server can replay the rest. |
| **MSW** | Mock Service Worker: intercepts requests at the network layer in tests, so the app's real client runs. |
| **axe** | An automated accessibility checker for WCAG rules. |
| **Skip link** | The first focusable link on the page, which jumps past the navigation to the main content. |
