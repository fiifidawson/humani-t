# ADR-0004: Chakra UI, with a per-response nonce in a strict CSP

- **Status:** Accepted
- **Date:** 2026-11-03

## Context

humani-t is a sub-project of the LiGHT lab, and the lab asked that it look visually linked to its other work and recommended Chakra UI for its web projects. Until now the web app was hand-written components over plain CSS files, which suited the strict Content Security Policy: `style-src 'self'` allowed only the built stylesheet.

Chakra UI v3 styles components at run time. Its style props are turned into rules by Emotion, which inserts them into `<style>` elements in the page. Under `style-src 'self'` every one of those elements is refused and the app renders unstyled. Something in the policy has to change, and the CSP is one of the main defences of a page that shows humanitarian case material ([UI_UX_SPEC §12–13](../UI_UX_SPEC.md), [architecture §5](../architecture.md)).

The policy also enforces Trusted Types (`require-trusted-types-for 'script'; trusted-types 'none'`), so any library that turns strings into HTML would break.

## Decision

**Use Chakra UI v3, and allow its styles with a nonce that is new on every response.**

- The build leaves a template, ``{{placeholder `http.request.uuid`}}``, wherever the page needs the nonce (Vite's `html.cspNonce`: the `<meta property="csp-nonce">` tag and every script and stylesheet tag).
- Caddy fills it in with its `templates` directive, scoped to the page alone (`/` and `/index.html`, after `try_files`), and sends `style-src 'self' 'nonce-{http.request.uuid}'`. Both come from the same request's UUIDv4, so they always match. No third-party Caddy module is needed. The page is `no-store`; hashed assets are never templated and stay immutable.
- In the browser, Emotion's cache is created with the nonce read from the meta tag and handed to Chakra through `CacheProvider`, so every `<style>` it adds carries the nonce.
- `script-src` stays `'self'` with no nonce: the nonce is for styles only. Every other directive, Trusted Types included, is unchanged.
- `vite preview` makes a fresh nonce per page the same way; the dev server uses a fixed one with a relaxed policy ([walkthrough 13, D13.11](../walkthroughs/13-web-ui.md)).
- `apps/web/csp.config.ts` builds the policy; a test fails if Caddy's copy differs, and the compose smoke test checks that the page's nonce matches the header and changes on every response.

## Alternatives considered

**Ark UI with plain CSS.** Ark is the headless layer under Chakra: the same accessible behaviour (focus traps, ARIA, keyboard handling) with no runtime styling, so `style-src 'self'` could have stayed. Rejected because the lab asked for Chakra, and because every component's styles, states and themes would have been hand-written again in CSS that nothing type-checks, which is the cost the move was meant to remove.

**Allow `'unsafe-inline'` for styles.** The smallest change. Rejected: it lets any injected `<style>` or `style=""` attribute run, which opens CSS-based data exfiltration (attribute selectors that load a URL per character) and UI redressing, and it would make the policy fail the security header check that the deployment already enforces.

**A nonce fixed at build time, or a hash of each style.** A build-time nonce is public in the built files, so it protects nothing. Hashes do not work for rules Emotion writes at run time, which differ with state.

**Panda CSS** (the same team's build-time engine). It would produce a static stylesheet and need no nonce at all, but it is a different library with a code generator, not Chakra; it remains an option if the bundle budget below cannot be met another way.

## Consequences

- **Bundle size.** The first load grows from 125 KB to 206 KB of gzipped JavaScript, about 72 KB of it Chakra, Ark UI, zag.js and Emotion. That is over the spec's 170 KB budget ([UI_UX_SPEC §13.1](../UI_UX_SPEC.md)). The budget has not been raised; the options (route splitting, native inputs instead of Ark-backed ones, Panda CSS, or a new budget) are for the owner to choose.
- **The nonce lives in Caddy.** The page must pass through Caddy's `templates` to work: a different proxy, a CDN or a cache in front would need the same per-response substitution, and must never cache `index.html`. A page served without substitution renders unstyled, which fails visibly rather than silently weakening the policy. Compression of the page is kept: it carries no attacker-controlled text, so BREACH does not apply.
- **Trusted Types stay enforced.** Chakra, Ark UI, Emotion and the zag.js machines in use write no HTML strings; Emotion's `insertRule` and React's style props are CSSOM, which Trusted Types do not cover. The sign-in page renders under the full policy in Edge, through both `vite preview` and Caddy, with no violation. One zag.js machine (number input, unused) touches `innerHTML`; using it would need checking.
- **Components** must still never use a `style` attribute or write `element.style`; `src/csp.test.ts` keeps failing if they do. The brand lives in one Chakra system ([`apps/web/src/theme/system.ts`](../../apps/web/src/theme/system.ts)).
