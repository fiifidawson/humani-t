"""Helpers for the document specs: real files built in memory, and a team that can upload them.

The files are real: a DOCX is a zip of WordprocessingML, a PDF has a content
stream a reader must parse. Hostile ones (an external entity, an entity bomb, a
zip bomb) are built the way an attacker would build them.

`make_document_team` is `project_support.make_team` with the document seams
chosen by the spec: by default the real fake scanner (it finds EICAR), the
in-thread local extractor and an in-memory file store that counts the
transactions open while it works, so a spec can prove none is held.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response
from pypdf import PdfReader, PdfWriter

from app.api.dependencies import build_providers
from app.domain.mfa import MfaPolicy
from app.extraction.base import DocumentExtractor
from app.extraction.local import LocalDocumentExtractor
from app.files.memory import MemoryFileStore
from app.main import create_app
from app.malware.base import MalwareScanner
from app.malware.fake import FakeMalwareScanner
from app.settings import Environment, Settings
from tests.project_support import (
    OUTSIDER_EMAIL,
    OWNER_EMAIL,
    PASSWORD,
    REVIEWER_EMAIL,
    VIEWER_EMAIL,
    Team,
    add_member,
    create_project,
    signed_in,
)
from tests.support import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ORIGIN,
    AuthHarness,
    FakeClock,
    ObservingEmail,
    ObservingHasher,
    TrackingStorage,
    csrf_headers,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)


def document_xml(*paragraphs: str) -> str:
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{_xml_text(p)}</w:t></w:r></w:p>'
        for p in paragraphs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )


def _xml_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def docx(
    *paragraphs: str,
    xml: str | None = None,
    extra: dict[str, bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr(
            "word/document.xml", xml if xml is not None else document_xml(*paragraphs)
        )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _pdf_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(*pages: str) -> bytes:
    """A PDF with one line of Helvetica text per page, built by hand with a correct xref."""
    count = len(pages)
    page_ids = [4 + 2 * i for i in range(count)]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{i} 0 R" for i in page_ids)
            + f"] /Count {count} >>"
        ).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        stream = f"BT /F1 12 Tf 72 720 Td ({_pdf_string(text)}) Tj ET".encode("latin-1")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
        ).encode()
        objects[page_id + 1] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n")
    xref = out.tell()
    size = max(objects) + 1
    out.write(f"xref\n0 {size}\n0000000000 65535 f \n".encode())
    for number in range(1, size):
        out.write(f"{offsets[number]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def encrypted_pdf(text: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf(text))))
    writer.encrypt(user_password="secret", owner_password="owner", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class CountingFileStore(MemoryFileStore):
    """The in-memory store, recording how many transactions were open at each call."""

    def __init__(self, storage: TrackingStorage) -> None:
        super().__init__()
        self._storage = storage
        self.open_units_seen: list[int] = []

    async def put(self, key: str, data: bytes) -> None:
        self.open_units_seen.append(self._storage.open_units)
        await super().put(key, data)

    async def get(self, key: str) -> bytes | None:
        self.open_units_seen.append(self._storage.open_units)
        return await super().get(key)

    async def delete(self, key: str) -> bool:
        self.open_units_seen.append(self._storage.open_units)
        return await super().delete(key)


class CountingScanner(FakeMalwareScanner):
    def __init__(self, storage: TrackingStorage, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._storage = storage
        self.open_units_seen: list[int] = []

    async def scan(self, content: bytes):  # type: ignore[no-untyped-def]
        self.open_units_seen.append(self._storage.open_units)
        return await super().scan(content)


@dataclass
class DocumentTeam:
    team: Team
    files: CountingFileStore
    scanner: MalwareScanner

    @property
    def harness(self) -> AuthHarness:
        return self.team.harness

    @property
    def owner(self) -> TestClient:
        return self.team.owner

    @property
    def reviewer(self) -> TestClient:
        return self.team.reviewer

    @property
    def viewer(self) -> TestClient:
        return self.team.viewer

    @property
    def outsider(self) -> TestClient:
        return self.team.outsider

    @property
    def project_id(self) -> str:
        return self.team.project_id

    def path(self, suffix: str = "") -> str:
        return self.team.path(f"/documents{suffix}")

    def upload(
        self,
        content: bytes,
        filename: str = "field_report.txt",
        browser: TestClient | None = None,
        content_type: str = "application/octet-stream",
    ) -> Response:
        client = browser or self.owner
        return client.post(
            self.path(),
            files={"file": (filename, content, content_type)},
            headers=csrf_headers(client),
        )

    def process(self) -> int:
        """Run the document worker until it finds nothing to do; return the busy turns."""
        processor = self.harness.providers.document_processor

        async def drain() -> int:
            done = 0
            for _ in range(50):
                if not await processor.run_once():
                    break
                done += 1
            return done

        return asyncio.run(drain())

    def uploaded(self, content: bytes, filename: str = "field_report.txt") -> str:
        response = self.upload(content, filename)
        assert response.status_code == 202, response.text
        document_id: str = response.json()["id"]
        return document_id

    def ready(self, content: bytes, filename: str = "field_report.txt") -> dict[str, Any]:
        """Upload and process a file; return the owner's review of it."""
        document_id = self.uploaded(content, filename)
        self.process()
        response = self.owner.get(self.path(f"/{document_id}/review"))
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body


def make_document_team(
    *,
    scanner: MalwareScanner | None = None,
    extractor: DocumentExtractor | None = None,
    clock: FakeClock | None = None,
    **settings_overrides: object,
) -> DocumentTeam:
    clock = clock or FakeClock()
    storage = TrackingStorage()
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "public_origin": ORIGIN,
        "masking_ner": "off",
    }
    values.update(settings_overrides)
    settings = Settings.model_validate(values)
    files = CountingFileStore(storage)
    chosen_scanner = scanner or CountingScanner(storage)
    providers = build_providers(
        settings,
        clock=clock,
        password_hasher=ObservingHasher(storage),
        storage=storage,
        email=ObservingEmail(storage),
        mfa_policy=MfaPolicy(is_required=False),
        file_store=files,
        malware_scanner=chosen_scanner,
        document_extractor=extractor or LocalDocumentExtractor(),
    )
    harness = AuthHarness(
        create_app(settings, providers=providers),
        providers,
        clock,
        ObservingHasher(storage),
        storage,
        ObservingEmail(storage),
    )
    harness.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, is_admin=True)
    for email in (OWNER_EMAIL, REVIEWER_EMAIL, VIEWER_EMAIL, OUTSIDER_EMAIL):
        harness.create_user(email, PASSWORD)
    owner = signed_in(harness, OWNER_EMAIL)
    project_id = create_project(owner)["id"]
    add_member(owner, project_id, REVIEWER_EMAIL, "reviewer")
    add_member(owner, project_id, VIEWER_EMAIL, "viewer")
    team = Team(
        harness=harness,
        owner=owner,
        reviewer=signed_in(harness, REVIEWER_EMAIL),
        viewer=signed_in(harness, VIEWER_EMAIL),
        outsider=signed_in(harness, OUTSIDER_EMAIL),
        admin=signed_in(harness, ADMIN_EMAIL, ADMIN_PASSWORD),
        project_id=project_id,
    )
    return DocumentTeam(team, files, chosen_scanner)


def stored_document(team: DocumentTeam, document_id: str) -> Any:
    async def read() -> Any:
        async with team.harness.providers.storage.unit_of_work() as uow:
            return await uow.documents.get(UUID(document_id))

    return asyncio.run(read())
