# 03 — Envelope encryption

> **Milestones:** M6 · **Status:** done · **Previous:** [02 — Configuration and persistence](02-configuration-and-persistence.md) · **Next:** [04 — Audit log](04-audit-log.md)

This phase built the machinery that will keep each project's data unreadable to anyone holding only the database, and that makes deleting a project complete: every scope (a project, from M10) gets its own random data key; data is sealed with AES-256-GCM and bound to the row it belongs in; the data key is stored only wrapped by a key-encryption key (KEK) that the operator holds outside the database; and destroying a scope's data key makes every copy of its ciphertext permanently unreadable. Nothing user-facing is encrypted yet — projects arrive in phase 07 — but the audit log ([04](04-audit-log.md)) already uses it for event details. It implements [`architecture.md` §5.4](../architecture.md#54-encryption) and brief rules 15 and 17.

## What this phase delivers

- **`ScopeEncryption`** (`app/application/encryption.py`): `create_scope`, `encrypt`, `decrypt`, `destroy_scope` (crypto-shred) and `rewrap_all` (KEK rotation), each working inside the caller's unit of work.
- **Two seams** (`app/crypto/base.py`): `KeyProvider` (wraps and unwraps data keys with the KEK; implemented by `LocalKeyProvider`, a versioned keyring from configuration) and `Cipher` (authenticated encryption; implemented by `AesGcmCipher`).
- **Domain rules** (`app/domain/encryption.py`): `EncryptionContext` (scope, purpose, record id → associated data), `WrappedDataKey` (destroyed exactly when `wrapped` is `None`), and the errors `KeyDestroyedError`, `UnknownScopeError`, `DecryptionError`.
- **Migration `0001`**: the `data_keys` table with a check constraint that mirrors the domain rule.
- **Operator commands:** `python -m app.cli generate-kek` and `python -m app.cli rewrap-keys`.
- **Setting:** `HUMANI_T_KEY_ENCRYPTION_KEYS` (`version:base64,…`), required whenever `HUMANI_T_DATABASE_URL` is set ([D2.2](02-configuration-and-persistence.md#d22-configuration-and-secrets-fail-closed)).

### Data model

| Table | Columns | Purpose and rules |
| --- | --- | --- |
| `data_keys` | `scope_id` (primary key), `kek_version`, `wrapped`, `created_at`, `destroyed_at` | One wrapped data key per scope. Check constraint `destroyed_iff_unwrapped`: `(wrapped IS NULL) = (destroyed_at IS NOT NULL)`. A destroyed key is never written again: every update carries `WHERE wrapped IS NOT NULL`. The row is kept after shredding, so the destruction itself stays on record. |

## Architecture choices and why

### D3.1 Envelope encryption

- **Decision:** each scope has its own random 256-bit **data key**. Data is encrypted with it using AES-256-GCM. The data key is stored only **wrapped** (itself encrypted with AES-256-GCM) by a **key-encryption key** that the operator holds as a secret and the application never stores.
- **Why:** this is how cloud KMS systems work. It gives per-project deletion (D3.4), cheap key rotation (only the small data keys are re-wrapped, not the data; D3.5), and it makes the database alone useless to a thief.
- **Rejected:** one key for everything (no per-project deletion; rotation means re-encrypting everything); database-level transparent encryption only (protects disks, not a stolen database dump or a backup).
- **Costs:** the KEK becomes the most important secret: losing it loses all encrypted data, and anyone holding it and a database copy can read everything.
- **Where:** [`app/application/encryption.py`](../../services/api/app/application/encryption.py), [`app/crypto/local_key.py`](../../services/api/app/crypto/local_key.py).
- **Proven by:** `tests/test_encryption.py::TestScopeEncryption`, `TestLocalKeyProvider`.

### D3.2 Every ciphertext is bound to where it belongs

- **Decision:** `encrypt` and `decrypt` take an `EncryptionContext(scope_id, purpose, record_id)` — the project, the table and column (`purpose`, for example `audit_events.details`), and the row. Its `associated_data()` is canonical JSON, `["<scope>","<purpose>","<record>"]`, passed to AES-GCM as associated data. The wrapped data key is likewise bound to its scope: `LocalKeyProvider` uses `scope_id.bytes` as associated data when wrapping and unwrapping.
- **Why:** associated data is authenticated but not encrypted; decryption fails if it differs. A ciphertext copied into another row, column or project — by a bug or by someone with database access — fails to decrypt instead of being silently accepted, and a wrapped key cannot be moved to another scope. Canonical JSON rather than a delimiter-joined string, because `("a:b","c")` and `("a","b:c")` would otherwise produce the same bytes.
- **Rejected:** no associated data (ciphertexts become interchangeable between rows); a delimiter-joined context string (ambiguous).
- **Proven by:** `TestEncryptionContext::test_associated_data_cannot_be_forged_by_moving_a_delimiter`, `TestScopeEncryption::test_ciphertext_copied_to_another_row_does_not_decrypt`, `test_ciphertext_copied_to_another_project_does_not_decrypt`, `TestLocalKeyProvider::test_a_wrapped_key_cannot_be_moved_to_another_scope`.

### D3.3 A versioned sealed format with random nonces

- **Decision:** a sealed value is `version (1 byte, 0x01) || nonce (12 random bytes) || ciphertext + 16-byte tag`. `open` refuses an unknown version byte and any ciphertext that fails authentication, raising `DecryptionError`.
- **Why:** the version byte lets the algorithm change later without guessing what an old value was written with. Random 96-bit nonces are safe up to roughly 2³² messages per key; one key per project keeps every scope far below that. Encrypting the same plaintext twice never gives the same ciphertext.
- **Rejected:** counter nonces (need coordinated state across workers); AES-CBC with a separate MAC (two primitives to get right instead of one AEAD).
- **Proven by:** `tests/test_encryption.py::TestAesGcmCipher` — tampering, wrong associated data, unknown format version, nonce uniqueness, and a hypothesis round-trip for any bytes.

### D3.4 Crypto-shredding, and its limits

- **Decision:** destroying a scope sets `wrapped` to null and records `destroyed_at`; the row stays. From then on `decrypt` raises `KeyDestroyedError` for every ciphertext of that scope, wherever copies of it exist. A destroyed key can never be written again: the Postgres `save` updates only `WHERE wrapped IS NOT NULL` and raises `ConcurrentUpdateError` if the key was destroyed in the meantime, so a key rotation that read the key just before the shred cannot revive it. An unknown scope is an error (`UnknownScopeError`), never a silently created new key.
- **Why:** brief rule 17 requires complete deletion, including from places that are hard to find and from the append-only audit log, which must not be edited ([04](04-audit-log.md)).
- **Limit:** database **backups** still contain the wrapped key until they expire, and anyone with a backup and the KEK can unwrap it. Deletion is complete only after the backup retention window; the deployment guide (M14) must say so (gap G11).
- **Costs:** a shredded scope's rows remain as unreadable bytes until something deletes them; that is fine — they are noise without the key.
- **Proven by:** `TestScopeEncryption::test_shredded_scope_is_permanently_unreadable`, `test_shredding_is_recorded_not_deleted`, `test_unknown_scope_is_an_error_not_a_new_key`, `tests/test_storage_contract.py::TestShreddingIsFinal`.

### D3.5 Key rotation by re-wrapping

- **Decision:** `HUMANI_T_KEY_ENCRYPTION_KEYS` is a keyring: `1:<base64>,2:<base64>`. The highest version wraps new data keys; any loaded version unwraps. Rotation is: generate a key, append it with a higher version, restart, then run `rewrap-keys`, which unwraps every live data key not already under the current version and re-wraps it under the current one, in one transaction, and records a `keys.rewrapped` audit event. Destroyed keys are skipped; a key shredded during the rewrap is left destroyed. The data itself is never touched.
- **Why:** rotation costs one small operation per project, not a re-encryption of everything, and old data stays readable throughout.
- **Rejected:** re-encrypting all data on rotation (slow, risky, unnecessary with envelopes).
- **Costs:** an old KEK must stay loaded until `rewrap-keys` has moved every key off it; removing it earlier makes those keys unreadable (`KEK version 1 is not loaded`). Each KEK must be exactly 32 bytes.
- **Proven by:** `TestScopeEncryption::test_old_data_still_decrypts_after_kek_rotation`, `test_rewrap_skips_destroyed_and_current_keys`, `TestLocalKeyProvider::test_parses_versioned_keys_and_wraps_with_the_newest`, `test_rejects_malformed_config`, `test_rejects_short_keys`, `tests/test_cli.py::test_rewrap_keys_runs_against_configured_storage`.

### D3.6 An operator command line, not endpoints

- **Decision:** `python -m app.cli` holds the actions that must never be reachable over the internet: `generate-kek`, `rewrap-keys`, and (from later phases) `verify-audit`, `create-admin`, `purge-expired`. `generate-kek` *prints* the key and stores nothing: where it goes (a Docker secret, a password manager) is the operator's decision, and the app must never keep a copy. Every command builds the providers and closes them in a `finally`.
- **Why:** needing shell access to the server is a stronger guard than any endpoint protection. The worker (M4) will run `verify-audit` and `purge-expired` on a schedule.
- **Rejected:** admin HTTP endpoints for key management (one more thing to protect, and a remote path to the most important secret).
- **Where:** [`app/cli.py`](../../services/api/app/cli.py).

### D3.7 Two seams, and encryption inside the caller's transaction

- **Decision:** `KeyProvider` and `Cipher` are separate Protocols because they change for different reasons: `KeyProvider` moves when the operator switches from a key file to Vault or a cloud KMS; `Cipher` moves only if the algorithm does. `ScopeEncryption` never opens its own unit of work: `create_scope(uow, …)`, `encrypt(uow, …)` and so on run in the caller's transaction.
- **Why:** a project is never committed without its key, nor a key without its project; a shred and its audit event commit together.
- **Costs:** callers must pass the unit of work explicitly.

### D3.8 Where the KEK comes from

- **Decision:** in production, `key_encryption_keys` arrives as a Docker secret file (`/run/secrets/key_encryption_keys`); in development, `scripts/dev_secrets.py` writes it into `secrets/key_encryption_keys` and `services/api/.env`. With no KEK configured *and* no database, the app generates one per process — acceptable only because in-memory data dies with the process too; Settings refuses a database without a KEK.
- **Why:** the KEK must never sit in the database it protects, nor in the repository.
- **Costs:** in development the KEK is in a plain file on your disk; that is acceptable for fictional data only.

### Deliberately not used

| Not used | Why not |
| --- | --- |
| PostgreSQL `pgcrypto` / transparent data encryption | Keys would live with, or be handed to, the database; no per-project deletion |
| A KMS or Vault by default | Would be an outbound dependency the operator did not choose; `KeyProvider` is the seam for one |

## Libraries

### cryptography 50.0.2

- **What it does:** cryptographic primitives backed by OpenSSL. We use `AESGCM` (authenticated encryption with associated data) and `AESGCM.generate_key(bit_length=256)` for keys; nonces come from `os.urandom`.
- **Why this one:** the de facto standard Python cryptography library, maintained by the Python Cryptographic Authority, with a deliberately hard-to-misuse API (the `hazmat` layer is used only for the one AEAD we need).
- **Alternatives:** PyNaCl/libsodium (excellent, different primitives such as XChaCha20-Poly1305 with 192-bit nonces); PyCryptodome (more footguns).
- **Trade-offs:** binary wheels; regular security releases must be kept up to date (Dependabot, M4).
- **What we gave up:** XChaCha's larger nonces, which would lift the 2³²-messages-per-key bound; not needed at one key per project.

## Files to read, in order

From the rules, to the primitives, to the use case, to the operator:

1. [`app/domain/encryption.py`](../../services/api/app/domain/encryption.py) — `EncryptionContext.associated_data` and its comment; `WrappedDataKey.__post_init__`, `destroyed`, `rewrapped`, `require_wrapped`.
2. [`app/crypto/base.py`](../../services/api/app/crypto/base.py) — why two Protocols.
3. [`app/crypto/aesgcm.py`](../../services/api/app/crypto/aesgcm.py) — the sealed format, the nonce comment, `DecryptionError` on any failure.
4. [`app/crypto/local_key.py`](../../services/api/app/crypto/local_key.py) — the keyring, `from_config`, wrapping bound to `scope_id.bytes`, `generate_kek`.
5. [`app/application/encryption.py`](../../services/api/app/application/encryption.py) — the five operations; why `rewrap_all` swallows `ConcurrentUpdateError`.
6. [`migrations/versions/20261024_0001_data_keys.py`](../../services/api/migrations/versions/20261024_0001_data_keys.py) — the constraint, and why it is in the database as well as the domain.
7. [`app/storage/postgres/storage.py`](../../services/api/app/storage/postgres/storage.py), `PostgresDataKeyRepository.save` — the `wrapped IS NOT NULL` condition.
8. [`app/cli.py`](../../services/api/app/cli.py) — `_generate_kek`, `_rewrap`.
9. [`tests/test_encryption.py`](../../services/api/tests/test_encryption.py) — read the test names as the specification.

## Set up

[00 — Setup](00-setup.md) through S0.12. Run the commands below from `services\api`. Python snippets are piped in as a here-string; the closing `'@` must be at the start of its line.

## Manual tests

### T3.1 Generate a key-encryption key

- **Do:** `uv run python -m app.cli generate-kek`, then `[Convert]::FromBase64String((uv run python -m app.cli generate-kek)).Length`
- **Expect:** a 44-character base64 string; then `32`.
- **Proves:** D3.6 — a correct 256-bit KEK, printed and not stored anywhere (check: no new file in the repository).
- **If it fails:** another length means `generate_kek` no longer produces an AES-256 key.

### T3.2 Watch envelope encryption, binding, rotation and shredding

Runs the real `ScopeEncryption`, `LocalKeyProvider` and `AesGcmCipher` against in-memory storage; touches no database.

- **Do:**

```powershell
@'
import asyncio, uuid
from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.domain.encryption import EncryptionContext
from app.storage.memory import MemoryStorage

async def main():
    storage = MemoryStorage()
    kek1 = generate_kek()
    enc = ScopeEncryption(LocalKeyProvider.from_config(f"1:{kek1}"), AesGcmCipher())
    project = uuid.uuid4()
    here = EncryptionContext(project, "notes.body", "row-1")
    elsewhere = EncryptionContext(project, "notes.body", "row-2")
    async with storage.unit_of_work() as uow:
        await enc.create_scope(uow, project)
        sealed = await enc.encrypt(uow, here, b"Field notes: Tamale clinic")
        await uow.commit()
    print("1. sealed bytes:", len(sealed), "first byte (format version):", sealed[0])
    async with storage.unit_of_work() as uow:
        print("2. decrypts in place:", await enc.decrypt(uow, here, sealed))
        try:
            await enc.decrypt(uow, elsewhere, sealed)
        except Exception as exc:
            print("3. copied to another row:", type(exc).__name__, "-", exc)
    rotated = ScopeEncryption(
        LocalKeyProvider.from_config(f"1:{kek1},2:{generate_kek()}"), AesGcmCipher()
    )
    async with storage.unit_of_work() as uow:
        print("4. rewrapped under KEK 2:", await rotated.rewrap_all(uow))
        await uow.commit()
    async with storage.unit_of_work() as uow:
        print("5. still decrypts after rotation:", await rotated.decrypt(uow, here, sealed))
        await rotated.destroy_scope(uow, project)
        await uow.commit()
    async with storage.unit_of_work() as uow:
        try:
            await rotated.decrypt(uow, here, sealed)
        except Exception as exc:
            print("6. after shredding:", type(exc).__name__, "-", exc)

asyncio.run(main())
'@ | uv run python -
```

- **Expect:**

```text
1. sealed bytes: 55 first byte (format version): 1
2. decrypts in place: b'Field notes: Tamale clinic'
3. copied to another row: DecryptionError - ciphertext failed authentication
4. rewrapped under KEK 2: 1
5. still decrypts after rotation: b'Field notes: Tamale clinic'
6. after shredding: KeyDestroyedError - data key for scope <uuid> was destroyed
```

  55 bytes = 1 version byte + 12 nonce bytes + 26 bytes of ciphertext (the plaintext's length) + 16 bytes of tag.
- **Proves:** D3.1 to D3.5 in one run: the format, binding to a row, rotation without touching data, and permanent shredding.
- **If it fails:** line 3 decrypting means associated data is no longer bound; line 6 decrypting means shredding does not work — stop and investigate before anything else.

### T3.3 Removing an old KEK too early

- **Do:**

```powershell
@'
import asyncio, uuid
from app.application.encryption import ScopeEncryption
from app.crypto.aesgcm import AesGcmCipher
from app.crypto.local_key import LocalKeyProvider, generate_kek
from app.domain.encryption import EncryptionContext
from app.storage.memory import MemoryStorage

async def main():
    storage = MemoryStorage()
    before = ScopeEncryption(LocalKeyProvider.from_config(f"1:{generate_kek()}"), AesGcmCipher())
    project = uuid.uuid4()
    here = EncryptionContext(project, "notes.body", "row-1")
    async with storage.unit_of_work() as uow:
        await before.create_scope(uow, project)
        sealed = await before.encrypt(uow, here, b"Field notes")
        await uow.commit()
    only_new = ScopeEncryption(LocalKeyProvider.from_config(f"2:{generate_kek()}"), AesGcmCipher())
    async with storage.unit_of_work() as uow:
        try:
            await only_new.decrypt(uow, here, sealed)
        except Exception as exc:
            print("old KEK removed too early:", type(exc).__name__, "-", exc)

asyncio.run(main())
'@ | uv run python -
```

- **Expect:** `old KEK removed too early: ValueError - KEK version 1 is not loaded`
- **Proves:** the cost in D3.5 — keep every old KEK until `rewrap-keys` has run.
- **If it fails:** any other outcome means the keyring is not version-aware.

### T3.4 Key rotation on the development database

- **Do:**
  1. `uv run python -m app.cli generate-kek` and copy the output.
  2. In `services\api\.env`, change `HUMANI_T_KEY_ENCRYPTION_KEYS=1:<old>` to `HUMANI_T_KEY_ENCRYPTION_KEYS=1:<old>,2:<new>`. Restart the API (`--reload` does not watch `.env`).
  3. `uv run --env-file .env python -m app.cli rewrap-keys`
  4. pgAdmin: `SELECT action, target_type, target_id, actor_id FROM audit_events WHERE action = 'keys.rewrapped' ORDER BY sequence DESC LIMIT 1;` and `SELECT kek_version, count(*) FROM data_keys WHERE wrapped IS NOT NULL GROUP BY kek_version;`
- **Expect:** `rewrapped 0 data key(s) under the current KEK` (0 until projects exist in M10; the command and its audit event still run). The audit row is `keys.rewrapped`, `kek`, `2`, with an empty `actor_id` (the system). The `data_keys` query returns no rows today; with projects it must show only version `2`. Keep **both** keys in `.env`.
- **Proves:** D3.5 and D3.6 — rotation is an operator action, recorded in the audit log.
- **If it fails:** an error on start-up after step 2 usually means a typo in the keyring (see T3.5).

### T3.5 A malformed or short KEK stops the start-up

- **Do:** in a second window: `$env:HUMANI_T_KEY_ENCRYPTION_KEYS = "1:not-base64"; uv run --env-file .env uvicorn app.main:app --port 8732 --no-proxy-headers`; then `$env:HUMANI_T_KEY_ENCRYPTION_KEYS = "1:AAAA"` and the same command; then `Remove-Item Env:HUMANI_T_KEY_ENCRYPTION_KEYS`.
- **Expect:** start-up fails with ``key_encryption_keys must be `version:base64,…` ``, then with `KEK version 1 must be 32 bytes`. (A variable already set in the shell takes precedence over `--env-file`.)
- **Proves:** a bad key is refused before anything is encrypted with it.
- **If it fails:** a server that starts with a short key would encrypt with a key of the wrong strength — or crash later, mid-request.

### T3.6 The database enforces "destroyed exactly when unwrapped"

In pgAdmin, connected as `humani_t_app` to `humani_t`:

| # | Run | Expect | Proves |
| --- | --- | --- | --- |
| T3.6.1 | `INSERT INTO data_keys VALUES (gen_random_uuid(), 1, 'x', now(), now());` | `violates check constraint "destroyed_iff_unwrapped"` | A key cannot be both present and destroyed, even outside the app |
| T3.6.2 | `INSERT INTO data_keys VALUES (gen_random_uuid(), 1, NULL, now(), NULL);` | the same violation | A key cannot be missing without being recorded as destroyed |
| T3.6.3 | `SELECT count(*) FROM data_keys;` | `0` | No scopes exist in development until projects (M10) |

- **If it fails:** a successful insert means migration `0001` did not run or the constraint was dropped; a non-zero count in `humani_t` means something wrote scopes into development data (the contract tests use `humani_t_test`).

### Results checklist

| Test | Passed | Notes |
| --- | --- | --- |
| T3.1 Generate a KEK | ☐ | |
| T3.2 Envelope, binding, rotation, shredding | ☐ | |
| T3.3 Old KEK removed too early | ☐ | |
| T3.4 Rotation on the dev database | ☐ | |
| T3.5 Malformed and short KEKs refused | ☐ | |
| T3.6 Database constraint | ☐ | |

Anything that does not match an **Expect** is a bug or a wrong test. Record the command, the output and any API log line. It gets a failing test before it gets a fix.

## Automated tests that prove it

| Claim | Test |
| --- | --- |
| Blank purposes refused; associated data cannot be forged by moving a delimiter | `tests/test_encryption.py::TestEncryptionContext` |
| Naive timestamps refused; a destroyed key hands out no bytes | `tests/test_encryption.py::TestWrappedDataKey` |
| Tampering, wrong associated data and unknown formats rejected; nonces never repeat; any bytes round-trip | `tests/test_encryption.py::TestAesGcmCipher` |
| Versioned keyring, newest wraps; malformed and short keys refused; a wrapped key cannot move scope | `tests/test_encryption.py::TestLocalKeyProvider` |
| Round trip; no copying between rows or projects; shredding permanent and recorded; unknown scope is an error; rotation keeps data readable; rewrap skips destroyed and current keys | `tests/test_encryption.py::TestScopeEncryption` |
| A stale copy cannot revive a destroyed key (memory and Postgres) | `tests/test_storage_contract.py::TestShreddingIsFinal` |
| Destroying a key is saved; saving an unknown key fails | `tests/test_storage_contract.py::TestDataKeys` |
| `generate-kek` prints 32 bytes; `rewrap-keys` runs | `tests/test_cli.py` |

```powershell
uv run pytest tests/test_encryption.py tests/test_cli.py -v
```

## Known gaps

| Id | Gap | Effect | Plan |
| --- | --- | --- | --- |
| G11 | Crypto-shredding does not reach backups until they expire | Deletion is complete only after the backup retention window | Document in the deployment guide (M14) |
| — | Only a file-based `LocalKeyProvider` exists | The KEK sits beside the deployment rather than in a KMS or HSM | Implement `KeyProvider` for Vault or a cloud KMS when an operator needs it |
| — | Nothing user-facing is encrypted yet | The machinery is proven by tests, not yet by real project data | Projects (M10, phase 07) create a scope per project |
| — | No check that every key has left an old KEK version before it is removed | An operator could drop an old KEK too early (T3.3) | A `rewrap-keys` summary per version, or a refusal to start when a live key's version is not loaded |
| — | Losing the KEK loses all encrypted data | Key custody is entirely the operator's responsibility | Key custody section in the deployment guide (M14) |

## Glossary

| Term | Meaning |
| --- | --- |
| **AAD / associated data** | Data bound into an encryption without being encrypted; decryption fails if it differs. Used to tie a ciphertext to its row. |
| **AEAD** | Authenticated encryption with associated data: encryption that also detects any change. AES-GCM is one. |
| **AES-256-GCM** | AES with a 256-bit key in Galois/Counter Mode: fast, authenticated encryption. |
| **Crypto-shredding** | Deleting data by destroying the key that encrypts it. |
| **Data key / KEK** | The key that encrypts data / the key-encryption key that encrypts data keys. |
| **Envelope encryption** | Encrypting data with a data key, and the data key with a KEK. |
| **Keyring** | Several versioned KEKs loaded at once: the newest wraps, any unwraps. |
| **Nonce** | A number used once per encryption with a given key; here 12 random bytes. |
| **Rewrap** | Decrypt a data key with an old KEK and encrypt it with the new one; the data is untouched. |
| **Scope** | Whatever is deleted as a unit, and so has its own data key: a project. |
| **Tag** | The 16-byte authenticator AES-GCM appends; a wrong tag means tampering or the wrong key or context. |
