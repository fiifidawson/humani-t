# ADR-0016: Collections are shared labels with no access rules of their own

- **Status:** Accepted
- **Date:** 2026-11-19

## Context

UI_UX_SPEC §7.3 groups projects into collections ("Sahel 2026", "Protection"), one level deep, and leaves open whether a collection has its own owners and members (§16 Q1). The spec assumes a label: any signed-in user creates one, and each project owner files their own project in at most one. A project stays the unit of access, encryption and deletion.

A collection is not inside any project, so its name cannot be sealed under a project's key, and it is seen by people who share no project. Its name can still say a lot ("Staff security, Gao"). And because one collection holds projects of different teams, anything that reports on its contents could reveal a project to someone outside it.

## Decision

- **A label, no access rules.** Any signed-in user may create a collection. Every signed-in user can see every collection's name, so a project owner can file into one someone else made. A collection grants nothing and reveals nothing about a project: its `project_count` counts only the caller's own live projects, and the project list only ever reads the caller's projects.
- **Who manages it.** Its creator, or an instance administrator tidying the deployment's labels, may rename or delete it. Filing a project in it, or taking one out, is the project owner's (`project.update`), never the collection's creator's.
- **Deleting it unfiles.** Deleting a collection leaves every project it held, anyone's, in "Not in a collection". Refusing while it held projects would tell the caller that projects they cannot see exist.
- **Stored sealed, under the collection's own key.** Each collection is its own encryption scope, as a project is: its name is sealed with associated data `(collection, "collections.name", collection)`, and deleting it destroys its key, so the name is unreadable in any backup. Placements (`project_collections`) hold ids only and cascade with the collection.
- **One name, one collection.** Names are compared trimmed, with spaces collapsed and case ignored; a name in use is a `409` with `collection_name_taken`. Sealed names cannot be kept unique by the database, so the check reads every name under a table lock (`SHARE ROW EXCLUSIVE`) that queues other creates and renames but not reads.
- **Tags** are a project's own text: one row per project, the list sealed under the project's key and changed by owners under the project's row lock. **Pins** are per user, ids only, stored plain because they are not project text; they go when the project is deleted or the person leaves it.

## Consequences

- Anyone in the deployment can read every collection name, so the create dialog says so. A name that must stay within a team belongs in a tag, which only the project's members see.
- A creator who renames a collection renames it for everyone using it. That is what a shared label means; an administrator can step in.
- Listing collections opens one key per collection. For the tens or hundreds a deployment will have, that is cheap; it is the cost of shredding names on delete.
- Moving to collections with owners and members later (§16 Q1) is additive: a membership table beside `collections`, and the count and list rules stay.

## Alternatives rejected

- **Collections with their own members:** a second access model beside projects, with its own invitations and last-owner rule, for a grouping feature. The spec's own assumption is a label.
- **Names in the clear:** a collection name can be as telling as a project name, and a backup would keep a deleted name forever.
- **One deployment-wide key for every name:** deleting a collection could not shred its name.
- **Per-user collections:** two people working on "Sahel 2026" would each keep their own, and the shared "everything for the Sahel operation" view in §7.3 would be lost.
