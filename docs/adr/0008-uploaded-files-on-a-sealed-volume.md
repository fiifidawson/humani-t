# ADR-0008: Uploaded files are kept sealed on a local volume, not in an object store

- **Status:** Accepted
- **Date:** 2026-11-05

## Context

P2 lets a team upload its own documents to a project ([UI_UX_SPEC §6.13](../UI_UX_SPEC.md#613-documents)). The file has to be kept until it is read, and kept afterwards so a correction can be traced to what was uploaded. [`architecture.md` §3.3](../architecture.md#33-filestorageprovider) asked for a file storage seam and deferred the choice of backend until uploads existed, along with malware scanning ([walkthrough 01](../walkthroughs/01-baseline-and-quality-gates.md) lists MinIO and ClamAV as "deferred until uploads exist; MinIO's licensing and image status also need checking").

Three things about this deployment shape the choice:

- **It is one self-hosted Compose stack**, run by a humanitarian organisation's operator, often a single host. Every extra service is something to patch, back up and secure.
- **The files are sensitive.** A backup or a disk image that holds them in the clear is a breach waiting to happen, and deleting a project must make its files unreadable (brief rule 17).
- **The volumes are small.** Quotas default to 200 documents and 1 GiB per project, 20 MiB per file.

The S3-compatible stores we would self-host have each moved since the plan was written. As checked for this ADR (2026-11, from the projects' own announcements; verify before relying on it):

- **MinIO** is AGPL-3.0. Since 2025 its community edition has been reduced (the admin console removed), official binaries and container images are no longer published for it, and the open-source repository was put into maintenance mode. Depending on it now means building and maintaining our own images of a project its vendor has stepped back from.
- **SeaweedFS** is Apache-2.0 and maintained, but it is a distributed system (master, volume and filer servers) built for scale we do not have.
- **Garage** is AGPL-3.0, maintained by Deuxfleurs and designed for small self-hosted clusters. It is the closest fit if we ever need one, and AGPL is acceptable for a separate service we do not modify, but it is still a second stateful service.

## Decision

- File storage is a seam: `FileStore` in `app/files/base.py` (`put`, `get`, `delete`; no listing, no pre-signed URLs), with `MemoryFileStore` as the fake and `LocalVolumeFileStore` as the implementation. The concrete store is chosen in `api/dependencies.py` from `HUMANI_T_DOCUMENT_STORAGE_DIR`.
- A file is stored at `<dir>/<project id>/<document id>.sealed`. The key is two UUIDs and nothing else, so a key can never become a path outside the store. Writes are atomic (temporary file, fsync, rename), never overwrite, and create files readable by the API's user only.
- **Only ciphertext reaches the store.** The application seals the whole file with the project's data key (AES-256-GCM, envelope encryption under the KEK) with associated data binding it to the project, the purpose `documents.file` and the document's id, before `put`. A file copied to another document's key fails to open; deleting the project destroys the key and every file of the project with it, wherever copies remain.
- With a database and no directory configured, uploads are switched off (`503` with a reason) rather than kept in memory, where they would vanish on restart while their rows survived.

## Consequences

- No new service, no new credentials, no new network path. The volume is backed up with the rest of the deployment's data, and its backups hold ciphertext only.
- Deleting one document deletes its file and rows. Copies of the file in backups stay decryptable with the project's live key until the backup retention period passes, exactly as for deleted rows (the deployment guide says so); deleting the project shreds them at once.
- The API process and the worker must share the volume. In Compose that is one named volume mounted into both; on several hosts it would need a shared filesystem.
- A file is held in memory whole while it is sealed, scanned and read (20 MiB by default). Streaming encryption would lift that bound; it is not needed at these sizes.

## When to revisit

Move to an S3-compatible store behind the same `FileStore` Protocol (Garage first, then SeaweedFS) when any of these holds:

- the API or the worker runs on more than one host, so a local volume is no longer shared;
- the operator wants file storage on separate hardware, or a deployment's documents outgrow one disk;
- per-file sizes grow beyond what is comfortable to hold in memory, which also calls for streaming encryption.

The stored bytes would not change: the same sealed blob under the same key.

## Alternatives rejected

- **MinIO:** licensing is acceptable, but the community edition's images are no longer published and the project is in maintenance mode; we would be adopting an unmaintained service to hold our most sensitive data.
- **Files as `bytea` in Postgres:** one fewer moving part, but it puts up to gigabytes per project into the database, its WAL and every database backup, and makes routine database maintenance slower for everyone.
- **Storing files unencrypted on an encrypted disk:** disk encryption protects a stolen disk, not a backup copied elsewhere, and cannot shred one project.
