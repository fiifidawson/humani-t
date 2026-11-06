"""Request pacing: how this process keeps to each source's published rate limit."""

from __future__ import annotations

import asyncio

import pytest

from app.net.pacing import Pacer


class FakeTime:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def time_() -> FakeTime:
    return FakeTime()


class TestPacer:
    async def test_the_first_request_does_not_wait(self, time_):
        pacer = Pacer(3.0, clock=time_.clock, sleep=time_.sleep)

        async with pacer.turn():
            pass

        assert time_.sleeps == []

    async def test_back_to_back_requests_are_spaced_by_the_interval(self, time_):
        """arXiv: no more than one request every three seconds."""
        pacer = Pacer(3.0, clock=time_.clock, sleep=time_.sleep)

        async with pacer.turn():
            time_.now += 0.5  # the request itself took half a second
        async with pacer.turn():
            pass

        assert time_.sleeps == [pytest.approx(3.0)]

    async def test_no_wait_once_the_interval_has_already_passed(self, time_):
        pacer = Pacer(3.0, clock=time_.clock, sleep=time_.sleep)

        async with pacer.turn():
            pass
        time_.now += 10
        async with pacer.turn():
            pass

        assert time_.sleeps == []

    async def test_requests_never_overlap(self):
        """arXiv also asks for a single connection at a time."""
        pacer = Pacer(0.0)
        in_flight = 0
        most_in_flight = 0

        async def request() -> None:
            nonlocal in_flight, most_in_flight
            async with pacer.turn():
                in_flight += 1
                most_in_flight = max(most_in_flight, in_flight)
                await asyncio.sleep(0.001)
                in_flight -= 1

        await asyncio.gather(*(request() for _ in range(5)))

        assert most_in_flight == 1

    def test_the_interval_cannot_be_negative(self):
        with pytest.raises(ValueError, match="interval"):
            Pacer(-1)
