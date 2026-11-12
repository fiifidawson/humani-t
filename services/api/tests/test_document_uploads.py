"""The upload request: one multipart file, CSRF enforced, every refusal before storage.

The upload is the one non-JSON body, so the origin checks and the session token carry the
CSRF defence. Type comes from content, and sizes, names and rate are limited, as in the
OWASP File Upload Cheat Sheet.
"""

from __future__ import annotations

import pytest

from app.api.uploads import UploadFormatError, parse_single_file
from app.crypto.local_key import generate_kek
from tests.document_support import DocumentTeam, docx, make_document_team, pdf
from tests.support import csrf_headers

BOUNDARY = "----humaniBoundary7MA4YWxkTrZu0gW"
TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def _body(*parts: tuple[str, str | None, bytes]) -> bytes:
    out = b""
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        out += (
            (
                f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n"
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    return out + f"--{BOUNDARY}--\r\n".encode()


class TestMultipartParser:
    def test_one_file_comes_back_with_its_name_and_exact_bytes(self):
        content = b"line one\r\n--not-the-boundary\r\nline two"

        upload = parse_single_file(TYPE, _body(("file", "notes.txt", content)))

        assert upload.filename == "notes.txt"
        assert upload.content == content

    def test_a_non_ascii_filename_survives(self):
        body = _body(("file", "rapport_été.txt", b"x"))

        assert parse_single_file(TYPE, body).filename == "rapport_été.txt"

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            (_body(("file", "a.txt", b"a"), ("file", "b.txt", b"b")), 422),
            (_body(("document", "a.txt", b"a")), 422),
            (_body(("file", None, b"a")), 422),
            (f"--{BOUNDARY}\r\nContent-Disposition: form-data\r\n\r\nx".encode(), 422),
            (b"no multipart framing at all", 422),
        ],
    )
    def test_anything_but_exactly_one_file_field_is_refused(self, body, status):
        with pytest.raises(UploadFormatError) as refused:
            parse_single_file(TYPE, body)
        assert refused.value.status == status

    @pytest.mark.parametrize(
        "content_type",
        ["application/json", "multipart/form-data", "multipart/form-data; boundary=a b "],
    )
    def test_a_missing_or_invalid_boundary_is_refused(self, content_type):
        with pytest.raises(UploadFormatError):
            parse_single_file(content_type, _body(("file", "a.txt", b"a")))

    def test_too_many_parts_are_refused(self):
        many = _body(*[("file", f"{i}.txt", b"x") for i in range(6)])

        with pytest.raises(UploadFormatError, match="too many parts"):
            parse_single_file(TYPE, many)


@pytest.fixture
def team() -> DocumentTeam:
    return make_document_team()


class TestCrossSiteRequests:
    def test_a_cross_site_upload_is_refused_before_the_route(self, team: DocumentTeam):
        response = team.owner.post(
            team.path(),
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers={**csrf_headers(team.owner), "Origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert team.files.files == {}

    def test_a_browser_reporting_a_cross_site_fetch_is_refused(self, team: DocumentTeam):
        response = team.owner.post(
            team.path(),
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers={**csrf_headers(team.owner), "Sec-Fetch-Site": "cross-site"},
        )

        assert response.status_code == 403

    def test_an_upload_without_the_csrf_token_is_refused(self, team: DocumentTeam):
        """A cross-site HTML form can send multipart; it cannot send this header."""
        response = team.owner.post(
            team.path(), files={"file": ("a.txt", b"hello", "text/plain")}
        )

        assert response.status_code == 403
        assert team.files.files == {}

    def test_multipart_is_refused_on_every_other_route(self, team: DocumentTeam):
        response = team.owner.post(
            team.team.path("/masking-rules"),
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers=csrf_headers(team.owner),
        )

        assert response.status_code == 415

    def test_a_form_encoded_upload_is_refused(self, team: DocumentTeam):
        response = team.owner.post(
            team.path(),
            content=b"file=hello",
            headers={
                **csrf_headers(team.owner),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        assert response.status_code == 415

    def test_a_same_origin_upload_with_the_token_is_accepted(self, team: DocumentTeam):
        response = team.upload(b"Minutes of the meeting.")

        assert response.status_code == 202


class TestRefusals:
    def test_the_type_comes_from_the_bytes_not_the_name_or_the_browser(
        self, team: DocumentTeam
    ):
        response = team.upload(pdf("hello"), "notes.txt", content_type="text/plain")

        assert response.status_code == 202
        assert response.json()["file_type"] == "pdf"

    def test_an_executable_named_txt_is_refused_and_nothing_is_stored(self, team: DocumentTeam):
        response = team.upload(b"MZ\x90\x00\x03\x00\x00\x00", "notes.txt")

        assert response.status_code == 415
        assert team.files.files == {}

    def test_an_empty_file_is_refused(self, team: DocumentTeam):
        assert team.upload(b"").status_code == 422

    def test_a_file_over_the_size_limit_is_refused(self):
        team = make_document_team(upload_max_bytes=2048, document_bytes_per_project=4096)

        response = team.upload(b"a" * 3000)

        assert response.status_code == 413
        assert team.files.files == {}

    def test_a_declared_body_over_the_limit_is_refused_before_it_is_read(self):
        team = make_document_team(upload_max_bytes=2048, document_bytes_per_project=4096)

        response = team.owner.post(
            team.path(),
            content=b"x" * 10,
            headers={
                **csrf_headers(team.owner),
                "Content-Type": TYPE,
                "Content-Length": str(10 * 1024 * 1024),
            },
        )

        assert response.status_code == 413

    def test_uploads_larger_than_a_json_body_are_accepted_on_the_upload_route(
        self, team: DocumentTeam
    ):
        response = team.upload(b"word " * 400_000)

        assert response.status_code == 202

    def test_the_document_quota_holds(self):
        team = make_document_team(documents_per_project=2)
        team.uploaded(b"one")
        team.uploaded(b"two")

        response = team.upload(b"three")

        assert response.status_code == 409
        assert len(team.files.files) == 2

    def test_the_byte_quota_holds(self):
        team = make_document_team(upload_max_bytes=4096, document_bytes_per_project=6000)
        team.uploaded(b"a" * 4000)

        response = team.upload(b"b" * 2500)

        assert response.status_code == 409

    def test_too_many_uploads_in_an_hour_are_throttled(self):
        team = make_document_team(uploads_per_hour=2)
        team.uploaded(b"one")
        team.uploaded(b"two")

        response = team.upload(b"three")

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_uploads_switched_off_by_the_operator_are_refused_with_the_reason(self):
        """A database without a document directory: the files would not survive a restart."""
        team = make_document_team(
            database_url="postgresql+asyncpg://app:secret@localhost/unused",
            key_encryption_keys=f"1:{generate_kek()}",
        )

        response = team.upload(b"minutes")
        listing = team.owner.get(team.path()).json()

        assert response.status_code == 503
        assert "storage directory" in response.json()["detail"]
        assert listing["uploads_enabled"] is False
        assert team.files.files == {}

    def test_a_reviewer_cannot_upload(self, team: DocumentTeam):
        assert team.upload(b"x", browser=team.reviewer).status_code == 403

    def test_a_non_member_gets_404_and_nothing_is_read(self, team: DocumentTeam):
        assert team.upload(b"x", browser=team.outsider).status_code == 404

    def test_a_docx_upload_keeps_a_safe_name(self, team: DocumentTeam):
        response = team.upload(docx("hello"), "../../evil‮xcod.docx")

        assert response.status_code == 202
        assert response.json()["name"] == "evilxcod.docx"
