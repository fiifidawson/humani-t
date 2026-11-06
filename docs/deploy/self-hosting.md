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
```

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

Every service runs as a non-root user, with a read-only filesystem, every Linux capability dropped, `no-new-privileges`, and memory, CPU and process limits, and has a health check; the worker's passes only while it keeps touching a heartbeat file. Images are pinned by digest. Postgres and Valkey sit on an internal network with no route to the internet and no published port. Secrets reach each container as files under `/run/secrets`, and each container receives only the ones it needs: `migrate` alone holds the schema-owner password; it never sees the key-encryption key.

## 2. Requirements

- **A server you control**, x86-64 or ARM64, running a current Linux (Debian 13, Ubuntu 24.04 or similar).
  - Without a local model: 2 vCPU, 4 GB RAM, 20 GB disk.
  - With the `local-model` profile on CPU: at least 4 vCPU and 16 GB RAM for the default `llama3.1:8b`, plus about 5 GB of disk for the model. A smaller model needs less; [the remote-model guide](remote-model.md#1-choose-a-machine-and-a-model) has a sizing table.
- **Docker Engine 25 or newer with the Compose plugin v2.24 or newer**, from [Docker's own repository](https://docs.docker.com/engine/install/). `docker compose version` must work.
- **Python 3.9 or newer**, only to run the secret generator once. Every current Linux distribution has it as `python3`.
- **For a public deployment:** a DNS name (an `A`, and ideally `AAAA`, record) pointing at the server, and ports 80 and 443 reachable from the internet. Caddy needs both to obtain a certificate from Let's Encrypt.
- **An SMTP server** for invitations, password resets and security notices: your organisation's, or a transactional mail provider.
- **Outbound access** from the server to Let's Encrypt, your mail server, the paper sources (OpenAlex, Europe PMC, arXiv), and the model server if it runs elsewhere.

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
| `COMPOSE_PROFILES` | Empty, or `local-model` to run Ollama in the stack. `local-mail` only for a trial. |
| `HUMANI_T_INFERENCE_URL`, `HUMANI_T_INFERENCE_MODEL` | The model server and model. The default is the bundled Ollama. To run the model on another machine, see [Running the model on another machine](remote-model.md). |

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
| Valkey | Nothing | Rate-limit counters and queued jobs; rebuilt on start by design. |
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

Run it daily from cron, copy the dumps to storage on another machine or provider (encrypted, for example with `age` or `restic`), and delete dumps older than your **retention window**: the number of days you keep backups. Choose that number deliberately. It is also the time a deleted project stays recoverable ([section 6](#6-deleting-data-what-crypto-shredding-does-and-does-not-do)).

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

## 10. Known limitations

- No independent security review yet. Do not use it with real personal data.
- Images are built on the server from source. There are no published, signed images yet.
- One server only: no high availability, and no built-in off-site backup.
- Valkey's password is a single shared credential; it does not yet use per-client ACLs.
