"""The file store and malware scanner seams: one behaviour for the fake and the real thing.

The file store contract runs against the in-memory store and a volume store on a temporary
directory. The ClamAV client talks to a small loopback clamd that speaks the real INSTREAM
protocol and finds EICAR, so it is tested byte for byte without a scanner installed.
"""

from __future__ import annotations

import asyncio
import struct
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from app.domain.documents import (
    MalwareScanRefusedError,
    MalwareScanUnavailableError,
    ScanOutcome,
)
from app.files.base import FileExistsInStoreError, FileStore, file_key
from app.files.memory import MemoryFileStore
from app.files.volume import LocalVolumeFileStore
from app.malware.clamd import ClamdAddress, ClamdScanner
from app.malware.fake import EICAR, FakeMalwareScanner
from app.malware.skip import NoMalwareScanner

KEY = file_key(uuid.uuid4(), uuid.uuid4())


@pytest.fixture(params=["memory", "volume"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> FileStore:
    if request.param == "memory":
        return MemoryFileStore()
    return LocalVolumeFileStore(tmp_path / "documents")


class TestFileStores:
    async def test_what_is_put_is_what_is_got(self, store: FileStore):
        await store.put(KEY, b"\x00sealed bytes")

        assert await store.get(KEY) == b"\x00sealed bytes"

    async def test_a_missing_file_is_none_not_an_error(self, store: FileStore):
        assert await store.get(KEY) is None

    async def test_a_stored_file_is_never_overwritten(self, store: FileStore):
        await store.put(KEY, b"first")

        with pytest.raises(FileExistsInStoreError):
            await store.put(KEY, b"second")
        assert await store.get(KEY) == b"first"

    async def test_delete_removes_the_file_and_says_whether_there_was_one(
        self, store: FileStore
    ):
        await store.put(KEY, b"x")

        assert await store.delete(KEY) is True
        assert await store.get(KEY) is None
        assert await store.delete(KEY) is False

    @pytest.mark.parametrize(
        "key",
        ["../../etc/passwd", f"{uuid.uuid4()}/../../x", f"{uuid.uuid4()}", "a/b", ""],
    )
    async def test_a_key_that_is_not_two_ids_is_refused(self, store: FileStore, key: str):
        with pytest.raises(ValueError, match="key"):
            await store.put(key, b"x")


class TestVolumeStore:
    async def test_files_sit_under_the_project_named_by_id_and_nothing_else_is_left(
        self, tmp_path: Path
    ):
        project, document = uuid.uuid4(), uuid.uuid4()
        store = LocalVolumeFileStore(tmp_path)

        await store.put(file_key(project, document), b"sealed")

        assert [p.name for p in (tmp_path / str(project)).iterdir()] == [f"{document}.sealed"]


class TestFakeScanner:
    async def test_it_finds_the_eicar_test_file_for_real(self):
        verdict = await FakeMalwareScanner().scan(b"prefix " + EICAR + b" suffix")

        assert verdict.outcome is ScanOutcome.INFECTED
        assert verdict.signature == "Eicar-Test-Signature"

    async def test_an_ordinary_file_is_clean(self):
        assert (await FakeMalwareScanner().scan(b"minutes")).outcome is ScanOutcome.CLEAN

    async def test_switched_off_it_is_unavailable(self):
        with pytest.raises(MalwareScanUnavailableError):
            await FakeMalwareScanner(is_available=False).scan(b"x")


class TestNoScanner:
    async def test_it_says_nothing_was_scanned(self):
        verdict = await NoMalwareScanner().scan(EICAR)

        assert verdict.outcome is ScanOutcome.NOT_SCANNED
        assert verdict.scanner == "none"


type Reply = Callable[[bytes], bytes]


def _clamd_reply(stream: bytes) -> bytes:
    if len(stream) > 1024 * 1024:
        return b"INSTREAM size limit exceeded. ERROR\0"
    if EICAR in stream:
        return b"stream: Eicar-Test-Signature FOUND\0"
    return b"stream: OK\0"


@pytest.fixture
async def clamd() -> AsyncIterator[tuple[ClamdAddress, list[bytes]]]:
    """A clamd on 127.0.0.1 that reads INSTREAM exactly as the real daemon does."""
    received: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        command = await reader.readuntil(b"\0")
        assert command == b"zINSTREAM\0"
        chunks = []
        while True:
            (length,) = struct.unpack("!L", await reader.readexactly(4))
            if length == 0:
                break
            chunks.append(await reader.readexactly(length))
        stream = b"".join(chunks)
        received.append(stream)
        writer.write(_clamd_reply(stream))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield ClamdAddress(host="127.0.0.1", port=port), received
    finally:
        server.close()
        await server.wait_closed()


class TestClamd:
    async def test_a_clean_file_is_streamed_whole_and_reported_clean(self, clamd):
        address, received = clamd
        content = b"x" * 200_000

        verdict = await ClamdScanner(address).scan(content)

        assert verdict.outcome is ScanOutcome.CLEAN
        assert verdict.scanner == "clamd"
        assert received == [content]

    async def test_an_infected_file_is_reported_with_its_signature(self, clamd):
        address, _ = clamd

        verdict = await ClamdScanner(address).scan(EICAR)

        assert verdict.outcome is ScanOutcome.INFECTED
        assert verdict.signature == "Eicar-Test-Signature"

    async def test_a_file_over_clamds_limit_is_refused_not_retried(self, clamd):
        address, _ = clamd

        with pytest.raises(MalwareScanRefusedError):
            await ClamdScanner(address).scan(b"x" * (2 * 1024 * 1024))

    async def test_an_unreachable_clamd_is_unavailable(self):
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()

        with pytest.raises(MalwareScanUnavailableError):
            await ClamdScanner(
                ClamdAddress(host="127.0.0.1", port=port), timeout_seconds=2
            ).scan(b"x")

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("tcp://clamav:3310", ClamdAddress(host="clamav", port=3310)),
            (
                "unix:///run/clamav/clamd.sock",
                ClamdAddress(socket_path="/run/clamav/clamd.sock"),
            ),
        ],
    )
    def test_addresses_are_tcp_or_a_unix_socket(self, address, expected):
        assert ClamdAddress.parse(address) == expected

    @pytest.mark.parametrize("address", ["clamav:3310", "http://clamav:3310", "tcp://clamav"])
    def test_any_other_address_is_refused(self, address):
        with pytest.raises(ValueError, match="clamd_address"):
            ClamdAddress.parse(address)
