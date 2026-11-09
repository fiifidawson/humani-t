# Self-hosting humani-t

This guide takes an operator from an empty server to a running humani-t, and then covers what keeps it safe over time: backups, key custody, upgrades, and the limits of deleting data. Read it all before the first real project goes in.

> humani-t has not yet had an independent security review. Until it has, run it with fictional or already-public data only.

Commands are for a Linux server's shell (bash) unless marked otherwise. `docker compose …` commands are the same in every shell; where Windows PowerShell 5.1 needs something different, for trying the stack on a Windows machine, that is shown too.

## 1. What runs

One Compose project, `humani-t-stack`, defined in [`compose.yaml`](../../compose.yaml):

```text
                 internet
                    |  80, 443 (TCP and UDP)
               [ caddy ]  HTTPS with automatic certificates; serves the web app;
                    |      forwards /api/* to the API; sets the security headers
     ---------------+--------------- backend (internal)
                    |
                 [ api ] ------------------------------ egress --> paper sources,
                    |                                   [ worker ]  mail server,
     ---------------+---------------- data (internal)       |       the model
          |                 |                               |
     [ postgres ]       [ valkey ]  <------------------------
          ^
     [ migrate ]  one-shot: applies database migrations, then exits

  optional profiles:  [ ollama ] local-model    [ mailpit ] local-mail
                      [ clamav ] [ freshclam ] malware-scan
```

With `malware-scan`, the worker reaches `clamav` on `scan`, an internal network nothing else is on. `freshclam` has a network of its own, with a route out to ClamAV's CDN and nothing else on it ([section 3.7](#37-scan-uploads-for-malware)).

| Service | Image | What it does |
| --- | --- | --- |
| `caddy` | built from [`apps/web/Dockerfile`](../../apps/web/Dockerfile) | The only service with published ports. Terminates HTTPS, serves the web app, proxies `/api`. |
| `api` | built from [`services/api/Dockerfile`](../../services/api/Dockerfile) | The FastAPI application. |
| `worker` | the same image | Background jobs: runs, scheduled clean-up. |
| `migrate` | the same image | Runs `alembic upgrade head` as the schema-owner role, then exits. `api` and `worker` start only after it succeeds. |
| `postgres` | `postgres:18.6-alpine` | The database. |
| `valkey` | `valkey/valkey:8.1.11-alpine` | Rate limits and the job queue. Holds nothing that needs a backup. |
| `ollama` | `ollama/ollama` (profile `local-model`) | Optional model server inside the deployment. |
| `mailpit` | `axllent/mailpit` (profile `local-mail`) | Optional fake mail server for trying the stack; never for production. |
| `clamav` | `clamav/clamav:1.5.4-debian` (profile `malware-scan`) | Optional. ClamAV's daemon: scans every uploaded document before anything reads it. |
| `freshclam` | the same image (profile `malware-scan`) | Optional. Keeps ClamAV's signatures current. |

Uploaded documents are kept in the `documents` volume, which `api` and `worker` share, sealed under each project's key ([ADR 0008](../adr/0008-uploaded-files-on-a-sealed-volume.md)).

Every service runs as a non-root user, with a read-only filesystem, every Linux capability dropped, `no-new-privileges`, and memory, CPU and process limits, and has a health check; the worker's passes only while it keeps touching a heartbeat file. Images are pinned by digest. Postgres and Valkey sit on an internal network with no route to the internet and no published port. Secrets reach each container as files under `/run/secrets`, and each container receives only the ones it needs: `migrate` alone holds the schema-owner password; it never sees the key-encryption key.

## 2. Requirements

- **A server you control**, x86-64 or ARM64, running a current Linux (Debian 13, Ubuntu 24.04 or similar).
  - Without a local model: 2 vCPU, 4 GB RAM, 20 GB disk.
  - With the `local-model` profile on CPU: at least 4 vCPU and 16 GB RAM for the default `llama3.1:8b`, plus about 5 GB of disk for the model. A smaller model needs less; [the remote-model guide](remote-model.md#1-choose-a-machine-and-a-model) has a sizing table.
  - With the `malware-scan` profile: about 1.5 GB more RAM and 1 GB more disk ([section 3.7](#37-scan-uploads-for-malware)).
- **Docker Engine 25 or newer with the Compose plugin v2.24 or newer**, from [Docker's own repository](https://docs.docker.com/engine/install/). `docker compose version` must work.
- **Python 3.9 or newer**, only to run the secret generator once. Every current Linux distribution has it as `python3`.
- **For a public deployment:** a DNS name (an `A`, and ideally `AAAA`, record) pointing at the server, and ports 80 and 443 reachable from the internet. Caddy needs both to obtain a certificate from Let's Encrypt.
- **An SMTP server** for invitations, password resets and security notices: your organisation's, or a transactional mail provider.
- **Outbound access** from the server to Let's Encrypt, your mail server, the paper sources (OpenAlex, Europe PMC, arXiv), the model server if it runs elsewhere, and, with the `malware-scan` profile, ClamAV's CDN (`database.clamav.net`).

> **Docker and host firewalls.** Ports published by Docker bypass `ufw` and `firewalld` rules on the host. Only Caddy publishes ports (80 and 443, which are meant to be public), and the optional Mailpit publishes on `127.0.0.1` only. Do not add a `ports:` line to any other service: a firewall rule will not protect it.

## 3. First run

### 3.1 Get the code

```bash
git clone https://github.com/fiifidawson/humani-t.git
cd humani-t
```

### 3.2 Create the secrets

Keep the secrets outside the checkout, so a `git clean`, a copied folder or a backup of the code cannot take them along:

```bash
sudo mkdir -p /etc/humani-t
sudo chown "$USER" /etc/humani-t
python3 infrastructure/generate_secrets.py --dir /etc/humani-t/secrets
```

The script writes one value per file: random passwords for the Postgres superuser, the schema-owner and runtime roles, and Valkey; a 256-bit key-encryption key (`key_encryption_keys`); the connection URLs built from those passwords; and an empty `smtp_password`. It never overwrites a file, so running it again is safe.

The directory is created `0700` (only you can enter it) and each file `0644`, so the unprivileged users inside the containers can read the files that are mounted into them. If your mail server needs a password, put it in `smtp_password`:

```bash
printf '%s' 'the-smtp-password' > /etc/humani-t/secrets/smtp_password
```

On Windows, for a local trial, `python infrastructure\generate_secrets.py` writes into `secrets\` inside the checkout, which is git-ignored.

### 3.3 Configure

```bash
cp .env.example .env
```

(PowerShell: `copy .env.example .env`.) Edit `.env`. Compose reads it automatically; it holds no secrets.

| Variable | Set it to |
| --- | --- |
| `HUMANI_T_DOMAIN` | Your host name, e.g. `humani-t.example.org`. `localhost` for a trial on one machine. |
| `HUMANI_T_PUBLIC_ORIGIN` | `https://` plus the domain, plus `:port` if it is not 443. The API refuses writes from any other origin. |
| `HUMANI_T_SMTP_HOST`, `_PORT`, `_USERNAME`, `_SENDER`, `_STARTTLS` | Your mail server. |
| `HUMANI_T_SECRETS_DIR` | `/etc/humani-t/secrets` (the `--dir` you used). |
| `COMPOSE_PROFILES` | Empty, or a comma-separated list: `local-model` to run Ollama in the stack, `malware-scan` to run ClamAV ([section 3.7](#37-scan-uploads-for-malware)), `local-mail` only for a trial. |
| `HUMANI_T_MALWARE_SCANNER`, `HUMANI_T_CLAMD_ADDRESS` | How uploaded documents are scanned. Until you choose, uploads are refused ([section 3.7](#37-scan-uploads-for-malware)). |
| `HUMANI_T_INFERENCE_URL`, `HUMANI_T_INFERENCE_MODEL` | The model server and model. The default is the bundled Ollama. To run the model on another machine, see [Running the model on another machine](remote-model.md). |
| `HUMANI_T_INFERENCE_API` | `ollama` (the default), or `openai` for a server that speaks OpenAI's Chat Completions: vLLM or llama.cpp on your own machine, or a hosted API (see [Running the model on another machine](remote-model.md#7-a-hosted-model-api-an-opt-in)). |
| `HUMANI_T_PAPER_SOURCES` | Where papers are searched, comma-separated: `openalex`, `europepmc`, `arxiv`. Empty searches nothing outside the deployment. |
| `HUMANI_T_MASK_COUNTRIES_AND_REGIONS` | `true` (the default) masks country and large-region names in questions like any place. `false` keeps them, so the search and the criteria keep the review's geography; towns, districts and camps stay masked ([ADR 0011](../adr/0011-countries-and-regions-as-an-operator-setting.md)). |

For a trial on one machine with no mail server, use the bundled Mailpit: `COMPOSE_PROFILES=local-mail`, `HUMANI_T_SMTP_HOST=mailpit`, `HUMANI_T_SMTP_PORT=1025`, `HUMANI_T_SMTP_STARTTLS=false`. Emails then appear at http://127.0.0.1:8025.

### 3.4 Start

```bash
docker compose up -d --build
docker compose ps
```

The first build takes a few minutes. Expect `caddy`, `api`, `worker`, `postgres` and `valkey` to be `healthy`, and `migrate` `exited (0)`. If something is not, `docker compose logs <service>` says why; the most common causes are in [section 9](#9-troubleshooting).

With `local-model`, download the model once (a few GB):

```bash
docker compose exec ollama ollama pull llama3.1:8b
```

### 3.5 Create the first administrator

There is no public sign-up. The first administrator is created from the server, and invites everyone else. The password is read from standard input only, never from an argument, which other users of the machine could see:

```bash
read -rs PASSWORD && printf '%s\n' "$PASSWORD" \
  | docker compose exec -T api python -m app.cli create-admin --email you@example.org --password-stdin; \
  unset PASSWORD
```

`read -rs` prompts without echoing what you type. In PowerShell:

```powershell
"the-passphrase-you-chose" | docker compose exec -T api python -m app.cli create-admin --email you@example.org --password-stdin
```

Expect `created admin you@example.org`. The password must be at least 15 characters. Open `https://<your domain>/`, sign in, and set up a passkey or an authenticator app when asked.

### 3.6 Check it

```bash
curl -sI https://humani-t.example.org/ | grep -i -E 'strict-transport|content-security|x-frame'
curl -s https://humani-t.example.org/api/v1/ready
```

Expect the three headers, and `{"status":"ready"}`. (With `localhost`, add `-k`: Caddy's local certificate authority is not trusted by your system.)

### 3.7 Scan uploads for malware

Teams can upload their own documents to a project, and each file is scanned before anything reads it. In production, uploads are refused until you choose how: the documents page says "Uploads are switched off: the operator has not configured a malware scanner."

**To scan with the bundled ClamAV** (recommended), set in `.env`:

```bash
COMPOSE_PROFILES=malware-scan            # added to any profiles already there
HUMANI_T_MALWARE_SCANNER=clamd
HUMANI_T_CLAMD_ADDRESS=tcp://clamav:3310
```

Then run `docker compose up -d`. Two more services start: `clamav` (the scanner) and `freshclam` (its updater). `clamav` takes a minute or two to load its signatures before it turns `healthy`. A document uploaded meanwhile waits at "Checking for viruses", and is scanned once clamd is up.

What it costs, and what it sends:

- **Memory:** clamd holds about 1 GiB once its signatures are loaded. Its limit is 2 GiB (`HUMANI_T_CLAMAV_MEMORY` in `.env`). freshclam idles at a few MiB, and needs about 0.7 GiB for the minute it spends testing a new database. Plan for about 1.5 GB more RAM than without scanning.
- **Disk:** about 1 GB for the image and the `clamav-signatures` volume.
- **Outbound traffic:** every two hours, freshclam asks ClamAV's CDN (`database.clamav.net`) for new signatures, and downloads the day's differences. The request names only database versions: no document, name or text leaves the deployment. `clamav` itself, which receives the uploaded files, has no route to the internet, and only the worker can reach it.

On a server with no internet access, run `clamav` alone (`docker compose up -d clamav`). It scans with the signatures built into its image, which grow staler every day. ClamAV's `cvdupdate` tool can keep a local mirror instead: point `DatabaseMirror` in [`infrastructure/clamav/freshclam.conf`](../../infrastructure/clamav/freshclam.conf) at it.

clamd accepts files up to 25 MiB (`StreamMaxLength` in [`infrastructure/clamav/clamd.conf`](../../infrastructure/clamav/clamd.conf)). If you raise `HUMANI_T_UPLOAD_MAX_BYTES` above that, raise `StreamMaxLength` too, and Caddy's limit for uploads in [`infrastructure/caddy/Caddyfile`](../../infrastructure/caddy/Caddyfile).

**When the scanner is down**, nothing is read unscanned. The worker puts each document back in the queue and tries again a few seconds later, and people see "Checking for viruses" until clamd answers. When freshclam cannot update, clamd keeps scanning with the signatures it has; after two days without a new daily database, `freshclam` shows as `unhealthy` in `docker compose ps`.

**To upload without scanning**, set `HUMANI_T_MALWARE_SCANNER=none` and leave `HUMANI_T_CLAMD_ADDRESS` empty. Every document then records `scanned_with: none`. Make that a written decision: a hostile file then reaches the text extractor unchecked ([ADR 0009](../adr/0009-local-extraction-in-an-isolated-process.md) describes how the extractor is contained).

The reasons for this setup are in [ADR 0012](../adr/0012-clamav-in-the-stack.md).

## 4. Key custody

humani-t encrypts each project's sensitive data with that project's own **data key**. The data keys are stored in the database, but only in wrapped (encrypted) form, under the **key-encryption key (KEK)** in `key_encryption_keys`. The KEK never touches the database.

That separation is the whole point. It means:

- **A stolen database backup on its own is unreadable.** Whoever has the backup also needs the KEK.
- **Losing the KEK loses every project.** There is no recovery: no back door, no reset. Without the KEK, the data keys cannot be unwrapped, and without the data keys, nothing encrypted can be read.

So the rules are:

1. **Back up the KEK at once, and separately from database backups.** A password manager or a sealed, offline copy held by a second person both work. Never store it next to, or in the same backup set as, the database dumps: a backup that holds both is as readable as no encryption at all.
2. **Restrict who can read it.** Anyone with the KEK and a database dump can read every project. On the server, only the operator account and root can enter the secrets directory.
3. **Know where every copy is.** You will need to destroy them all for the emergency erasure in [section 6](#6-deleting-data-what-crypto-shredding-does-and-does-not-do).

### Rotating the KEK

Rotate it on a schedule (yearly is reasonable), when someone who had access leaves, or if a copy may have leaked. Old and new versions sit side by side, so nothing becomes unreadable during the change:

```bash
docker compose exec api python -m app.cli generate-kek
```

This prints a new key and stores it nowhere. Append it to the file as the next version, so it reads `1:<old key>,2:<new key>` on one line, then:

```bash
docker compose up -d --force-recreate api worker
docker compose exec api python -m app.cli rewrap-keys
```

The highest version wraps every new data key, and `rewrap-keys` re-wraps the existing ones under it (`rewrapped N data key(s) under the current KEK`). The data itself is not re-encrypted; it does not need to be.

Keep the old version in the file, and in your KEK backup, for as long as you keep database backups made before the rotation: restoring one of those needs the KEK it was made under. Removing a version for good is how you make older backups unreadable on purpose ([section 6](#6-deleting-data-what-crypto-shredding-does-and-does-not-do)).

## 5. Backups and restore

What needs backing up:

| What | How | Notes |
| --- | --- | --- |
| The database | `pg_dump`, below | Everything humani-t stores. |
| The KEK | Once, and after each rotation | Separately from the database ([section 4](#4-key-custody)). |
| The other secret files | Once | Needed to restore onto a new server with the same configuration; new ones can also be generated. |
| `.env` | Once | Not secret. |
| Uploaded documents (`documents` volume) | `tar`, below | Sealed under each project's key. The files are useless without the database and the KEK, and the database's document rows are useless without the files: back them up together. |
| Valkey | Nothing | Rate-limit counters and queued jobs; rebuilt on start by design. |
| ClamAV's signatures (`clamav-signatures` volume) | Nothing | freshclam downloads them again. |
| Caddy's certificates (`caddy-data` volume) | Optional | Re-issued automatically; backing them up only avoids Let's Encrypt's rate limits on a rebuild. |

### Taking a backup

```bash
docker compose exec -T postgres pg_dump -U postgres --format=custom humani_t \
  > "humani_t-$(date +%Y%m%d-%H%M%S).dump"
```

On Windows PowerShell 5.1, redirect through `cmd`, because PowerShell's own `>` re-encodes the output as text and corrupts a binary dump:

```powershell
cmd /c "docker compose exec -T postgres pg_dump -U postgres --format=custom humani_t > humani_t.dump"
```

Back up the uploaded documents at the same time:

```bash
docker compose exec -T api tar -C /var/lib/humani-t -cf - documents \
  > "documents-$(date +%Y%m%d-%H%M%S).tar"
```

(PowerShell: `cmd /c "docker compose exec -T api tar -C /var/lib/humani-t -cf - documents > documents.tar"`.)

Run both daily from cron, copy the dumps and the archives to storage on another machine or provider (encrypted, for example with `age` or `restic`), and delete those older than your **retention window**: the number of days you keep backups. Choose that number deliberately. It is also the time a deleted project stays recoverable ([section 6](#6-deleting-data-what-crypto-shredding-does-and-does-not-do)).

A backup you have never restored is a hope, not a backup. Restore one onto a scratch machine regularly.

### Restoring

On a new server (or the same one after a loss), with the same secret files, or at least the same KEK:

```bash
docker compose up -d postgres
docker compose exec -T postgres pg_restore -U postgres --clean --if-exists \
  --single-transaction --exit-on-error --dbname humani_t < humani_t-20261103-020000.dump
docker compose up -d
docker compose exec api python -m app.cli verify-audit
```

Then restore the uploaded documents from the backup taken at the same time: `docker compose exec -T api tar -C /var/lib/humani-t -xf - < documents-20261103-020000.tar`.

Postgres creates the two roles and the empty `humani_t` database when its volume is first created; `pg_restore` then replaces the contents. When the rest of the stack starts, `migrate` brings an older backup's schema up to the current version. `verify-audit` should report `audit chain intact`; if it does not, the restored data does not match the audit log, and that needs investigating before the system is used.

PowerShell: `cmd /c "docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --single-transaction --exit-on-error --dbname humani_t < humani_t.dump"`.

## 6. Deleting data: what crypto-shredding does and does not do

When a project is deleted, humani-t destroys its data key. Every encrypted field of that project, in the live database, becomes permanently unreadable at that moment: the ciphertext is still there, but the key that opens it no longer exists anywhere in the database. This is **crypto-shredding**. Its strength is that it works without finding and overwriting every copy of every row.

Its limit is **backups**. A dump taken before the deletion still holds the project's wrapped data key and its ciphertext. Anyone with that dump **and** the KEK it was made under can restore it and read the project. Crypto-shredding cannot reach into a backup that was already taken.

So a deleted project is truly gone only once one of these is true:

1. **Every backup made before the deletion has been deleted**, because the retention window has passed. This is the normal path. Tell your users plainly: "a deleted project can be recovered from backups for N days, and after that it cannot be recovered by anyone".
2. **The KEK those backups were made under has been destroyed**, every copy of it, after rotating to a new one and running `rewrap-keys`. This is the emergency path, for when data must be unrecoverable now. It is blunt: it makes **every** backup from before the rotation unreadable, for every project, not only the deleted one.

What crypto-shredding does not cover:

- **Unencrypted metadata.** Identifiers, timestamps, and the hash-chained audit log's structure stay, by design: the audit trail must still verify after a project is deleted. Audit details are encrypted with the project's key, so they become unreadable with it.
- **Anything that left the deployment.** Exports downloaded by users, emails already sent, and text already sent to a model server (masked, but sent).
- **Container logs.** Application logs carry identifiers, not content, and are rotated by Docker (`max-size` 10 MB, 5 files per container).
- **The disk space of uploaded files.** A deleted project's files stay in the `documents` volume, unreadable without the destroyed key, until they are swept away. No sweep exists yet. Deleting a single document deletes its file at once.

## 7. Upgrades and migrations

```bash
# 1. Back up first, always.
docker compose exec -T postgres pg_dump -U postgres --format=custom humani_t > pre-upgrade.dump
# 2. Get the new version.
git pull
# 3. Rebuild with fresh base images, and restart.
docker compose build --pull
docker compose up -d
docker compose ps
```

`migrate` runs before the new `api` and `worker` start, and they start only if it succeeds. If it fails, `docker compose up` stops there and the database is unchanged: Alembic applies all pending migrations in one transaction, and Postgres rolls back schema changes with it. `docker compose logs migrate` says what went wrong, and `docker compose ps` shows which containers are still running.

**Rolling back** means checking out the previous version and restoring the backup from step 1. Migrations are not guaranteed to be reversible, so do not run an older version against a newer schema.

**Postgres major versions** (18 to 19, for example) cannot open each other's data directories. Dependabot never proposes one. When one is due: dump, stop the stack, remove the `postgres-data` volume, change the image in `compose.yaml`, start Postgres alone (`docker compose up -d postgres`), and restore the dump as in [section 5](#restoring).

## 8. Running it

| Task | Command |
| --- | --- |
| State of every service | `docker compose ps` |
| Logs of one service | `docker compose logs --tail 100 api` |
| Is the API up, can it reach the database | `curl -s https://<domain>/api/v1/health`, `…/api/v1/ready` |
| Check the audit log has not been tampered with | `docker compose exec api python -m app.cli verify-audit` (weekly, from cron) |
| Delete expired sessions and links now | `docker compose exec api python -m app.cli purge-expired` (the worker also schedules this) |
| Restart after changing `.env` or a secret file | `docker compose up -d --force-recreate api worker` |

**Client addresses.** Per-address throttling relies on Caddy passing the client's IP to the API. On Linux, Docker preserves it. On Docker Desktop (Windows and macOS) every request appears to come from Docker's gateway address, so per-address limits apply to all clients together; per-account limits are unaffected. Production belongs on Linux.

## 9. Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `required variable HUMANI_T_SMTP_HOST is missing a value` | No `.env`, or the variable is empty | [Section 3.3](#33-configure) |
| `secret … file … not found` (or `no such file`) | `HUMANI_T_SECRETS_DIR` does not point at the generated files | Check the path; re-run the generator with the same `--dir` |
| `migrate` exits non-zero with `password authentication failed` | The secret files were regenerated after the Postgres volume was created; the roles still have the old passwords | Restore the old secret files. On a fresh install with nothing to keep: `docker compose down -v` deletes **all data**, then start again |
| `api` restarts with `production requires: …` | A secret file is missing or empty, or `HUMANI_T_PUBLIC_ORIGIN` is not `https://` | `docker compose logs api` names the setting |
| Browser certificate warning on a real domain | Caddy could not get a certificate: DNS not pointing here, or port 80/443 blocked | `docker compose logs caddy` |
| `403 Cross-site requests are not allowed` on every sign-in | `HUMANI_T_PUBLIC_ORIGIN` differs from the address in the browser | Make them identical, including the port |
| `postgres` fails with `Permission denied` on a secret | Secret file not readable by the container's user | `chmod 0644` the files; keep the directory `0700` |
| "Uploads are switched off: the operator has not configured a malware scanner" | `HUMANI_T_MALWARE_SCANNER` is empty | [Section 3.7](#37-scan-uploads-for-malware), then `docker compose up -d --force-recreate api worker` |
| Documents stay at "Checking for viruses" | The worker cannot reach clamd: the `malware-scan` profile is off, or `clamav` is still loading or has stopped. `docker compose logs worker` shows `malware_scanner_unavailable` | `docker compose ps clamav` and `docker compose logs clamav`. If clamd was stopped for lack of memory, raise `HUMANI_T_CLAMAV_MEMORY` |
| `freshclam` is `unhealthy` | No new signatures for two days: outbound access to `database.clamav.net` is blocked, or ClamAV's CDN is refusing this address | `docker compose logs freshclam`; allow the traffic, or use a mirror ([section 3.7](#37-scan-uploads-for-malware)) |

## 10. Known limitations

- No independent security review yet. Do not use it with real personal data.
- Images are built on the server from source. There are no published, signed images yet.
- One server only: no high availability, and no built-in off-site backup.
- Valkey's password is a single shared credential; it does not yet use per-client ACLs.
