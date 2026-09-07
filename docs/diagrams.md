# Diagrams

Files live in [`diagrams/`](./diagrams/). Open a `.excalidraw` file with the
Excalidraw VS Code extension, or drag it onto <https://excalidraw.com>.

| Diagram | File |
| --- | --- |
| System architecture — layers, the ten provider seams, V1 implementations | [`diagrams/01-system-architecture.excalidraw`](./diagrams/01-system-architecture.excalidraw) |
| Review pipeline — question to cited synthesis, including the vocabulary branch | [`diagrams/02-review-pipeline.excalidraw`](./diagrams/02-review-pipeline.excalidraw) |
| V1 to V2 — what is shared, what is only an implementation swap | [`diagrams/03-v1-to-v2.excalidraw`](./diagrams/03-v1-to-v2.excalidraw) |

## Editing

Two options, and they do not mix well:

**Edit the file directly.** Open the `.excalidraw`, change it, save. Simplest for
small fixes. The matching source in `diagrams/src/` is then stale.

**Edit the source and rebuild.** Change `diagrams/src/<name>.json`, then:

```bash
python scripts/excalidraw_build.py
```

Better for structural changes, since the sources are compact and readable while
the built files are ~45KB of generated JSON.

Pick one per diagram and stay with it.

## Why there is a build step

The sources use a `label` shorthand on shapes. Excalidraw has no such field — a
labelled shape is really *two* elements: the container, and a text element bound
to it by `containerId`, with the container naming the text in `boundElements`.

[`scripts/excalidraw_build.py`](../scripts/excalidraw_build.py) does that
expansion and fills in the properties Excalidraw expects. Skipping it produces
shapes with no visible text.

## Reading them

**System architecture** — purple seams (`VectorStore`, `PaperSearch`, `Screening`,
`Sanitization`) are specific to evidence synthesis. Teal seams would exist in any
application. The purple ones are what make this not a chat app.

**Review pipeline** — the dashed red box between extraction and indexing is
[open work](./architecture.md), not a component that exists.

**V1 to V2** — the green band is written once. Every row below the divider is one
Protocol with two implementations. If something lands in the lower half that
should have been in the upper half, a seam is missing.

## These are not the source of truth

[`architecture.md`](./architecture.md) is. When the two disagree, the document
wins and the diagram needs regenerating.
