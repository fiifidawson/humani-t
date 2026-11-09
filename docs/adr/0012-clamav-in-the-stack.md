# ADR-0012: ClamAV runs in the compose stack as an opt-in profile, split from its updater

- **Status:** Accepted (the default is open for the owner, below)
- **Date:** 2026-11-09

## Context

Every uploaded file is scanned before it is read, or the operator writes down that it is not ([walkthrough 16, D16.6](../walkthroughs/16-documents.md#d166-scanning-is-a-decision-the-operator-writes-down)). The API already speaks clamd's `INSTREAM` protocol (`app/malware/clamd.py`), and production refuses uploads while `HUMANI_T_MALWARE_SCANNER` is unset (`Settings.uploads_disabled_reason`). What was missing was a clamd to talk to: an operator of the compose stack had to run their own, or choose `none` (gap G16.7). The stack also had no volume for uploaded files, so uploads were off there whatever the scanner.

Four things shape the choice:

- **clamd receives raw files**, before anything has checked them. It must be inside the deployment, and a compromised clamd should be able to reach as little as possible.
- **Signatures go stale within days.** ClamAV publishes new ones several times a day, and `freshclam` fetches them from ClamAV's CDN (`database.clamav.net`). That is outbound traffic to a destination the operator did not otherwise choose. It carries no user data (freshclam asks which database versions exist and downloads them), but [the one rule](../../CLAUDE.md#the-one-rule) asks that anything leaving the deployment be the operator's choice.
- **clamd is large.** With today's signatures it holds about 1 GiB in memory once loaded, and takes 20 to 60 seconds to load them. The stack's minimum without a local model is 4 GB of RAM.
- **The image wants root.** The official image's `/init` creates `/run/clamav`, changes the owner of the database directory and rewrites its config files with `sed -i`, all as root, then starts freshclam and clamd together in one container.

## Decision

- **Image:** the official `clamav/clamav`, tag `1.5.4-debian`, pinned by digest (`sha256:76a9c3b6…`), like every image in the stack. The Debian variant, because the Alpine one is published for amd64 only and the stack supports ARM64 servers. The tag with signatures built in, not `_base`, so a new volume starts with a usable database: clamd can start before the first download, or without one on a host with no internet.
- **Two services from the one image, both in the `malware-scan` profile:**
  - `clamav` runs `clamd` alone. It is on `scan`, an internal network shared only with the worker, so it has no route to the internet, and the API (the service the internet talks to through Caddy) cannot reach it either. It mounts the signature volume read-only.
  - `freshclam` runs `freshclam --daemon` alone, on `signatures`, a network of its own with a route out and nothing else on it. It never sees an uploaded file. clamd picks up its updates by checking the database directory every 10 minutes (`SelfCheck 600`), so freshclam needs no route to clamd.
- **Hardening, all of the stack's:** both run as the image's `clamav` user (`1000:1000`), with a read-only root filesystem, every capability dropped and `no-new-privileges`. The image's `/init` is bypassed: each service starts its daemon directly with our config, mounted read-only from `infrastructure/clamav/`. Writable places are the named volume `clamav-signatures` (freshclam only) and a `tmpfs` at `/tmp`. clamd writes each upload there before scanning it and unpacks archives there, so it is in memory and never reaches the host's disk unsealed. The CI smoke test checks clamd like every other container, with no exception.
- **clamd's own settings:** `StreamMaxLength 25M`, above the 20 MiB upload limit; `AlertExceedsMax yes`, so a file too large or too deeply nested to scan whole is reported rather than passed as clean; `ConcurrentDatabaseReload no`, which halves clamd's memory at the cost of scans waiting a few seconds during a reload; and the `SHUTDOWN`, `RELOAD` and `STATS` commands switched off, so nothing that can reach port 3310 can stop it.
- **freshclam checks 12 times a day** (every two hours; ClamAV asks for at most once an hour) and downloads daily differences rather than whole databases.
- **Opt-in, not on by default.** `COMPOSE_PROFILES=malware-scan` starts both services; `HUMANI_T_MALWARE_SCANNER=clamd` and `HUMANI_T_CLAMD_ADDRESS=tcp://clamav:3310` in `.env` point the API and the worker at it. Without them, production keeps refusing uploads, with the reason on screen, until the operator chooses `clamd` or `none`.
- **Uploads get their volume:** `documents`, mounted at `/var/lib/humani-t/documents` in the API and the worker, with `HUMANI_T_DOCUMENT_STORAGE_DIR` pointing at it. The API image creates that directory owned by its user, so a new volume starts with that owner.

## When the scanner is down

Scanning fails closed. When clamd cannot be reached, is still loading its signatures, or answers with an error, the worker puts the document back in the queue unread (`MalwareScanUnavailableError`, status `uploaded`), logs `malware_scanner_unavailable`, and tries again on its next poll, without limit. Nothing is read, masked or shown until a scan succeeds; the person sees "Checking for viruses" for as long as that takes. A file clamd refuses as too large fails as `scan_failed`, and an infected one is deleted unread (`malware_found`).

When freshclam cannot update, clamd keeps scanning with the signatures it has, which grow staler each day. The `freshclam` service turns unhealthy once the daily database is more than two days old, so `docker compose ps` shows it.

The API and the worker do not wait for clamd to start: a document uploaded in the first minute waits in the queue instead.

## Open for the owner

**On by default, or opt-in?** This ADR takes the safer side for the one rule: opt-in. A deployment talks to ClamAV's CDN only after the operator turns the profile on, and a small server is not asked for another 1.5 GB of memory unasked. The cost is one more step before the first upload in production, though the screen says why uploads are off. Making `malware-scan` the default would mean adding it to `COMPOSE_PROFILES` in `.env.example` and setting the two variables there. That changes what every new deployment sends out, so it is the owner's call.

## Consequences

- A deployment with the profile on needs about 1.5 GB more memory: clamd holds about 1 GiB (limit 2 GiB, raise it with `HUMANI_T_CLAMAV_MEMORY`), and freshclam reaches about 0.7 GiB for the minute it spends testing a new database (limit 1.5 GiB), then idles at a few MiB.
- Outbound traffic: freshclam's requests to `database.clamav.net`, every two hours. Nothing else from the scanner leaves the deployment. An air-gapped deployment can run `clamav` without `freshclam` (`docker compose up -d clamav`), on the signatures in the image, or mirror the database itself (ClamAV's `cvdupdate`) and point `DatabaseMirror` at the mirror.
- Pinning the digest pins the starting signatures to the day that image was built. They matter only until freshclam's first update, and Dependabot proposes newer digests for the image like any other.
- The EICAR test file is reported only when it is the whole file, or at its start, as the EICAR standard defines it. `FakeMalwareScanner` finds it anywhere in the file, so a manual test against real clamd must upload the bare test string.

## Rejected

- **The image's own `/init`, as root with `CHOWN`, `SETUID` and `SETGID`:** it would run freshclam and clamd in one container, so the process that reads hostile files would also hold a route to the internet.
- **`NotifyClamd`, so freshclam tells clamd to reload at once:** freshclam would need a route to clamd, and clamd's `RELOAD` command would have to stay on. A reload up to 10 minutes later is enough.
- **The Alpine image:** amd64 only.
- **The `_base` tag (no signatures in the image):** a new deployment could not scan anything until a ~110 MB first download finished, and an air-gapped one never could.
- **A hosted scanning API:** it would send raw files out of the deployment.
