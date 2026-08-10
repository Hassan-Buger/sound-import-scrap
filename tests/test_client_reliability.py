import asyncio

import pytest

from scraper.client import HttpClient, NonRetryableHttpError


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def text(self):
        return self._body


class FakeSession:
    closed = False

    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *_args, **_kwargs):
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_429_honors_retry_path_and_records_stats(monkeypatch):
    client = HttpClient(max_retries=2, request_delay=0)
    session = FakeSession(
        [
            FakeResponse(429, "slow down", {"Retry-After": "0"}),
            FakeResponse(200, '{"ok": true}'),
        ]
    )

    async def get_session():
        return session

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(client, "_get_session", get_session)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    assert await client.fetch_json("/test") == {"ok": True}
    assert client.stats == {"requests": 2, "failures": 0, "retries": 1}


@pytest.mark.asyncio
async def test_permanent_404_is_not_retried(monkeypatch):
    client = HttpClient(max_retries=5, request_delay=0)
    session = FakeSession([FakeResponse(404, "not found")])

    async def get_session():
        return session

    monkeypatch.setattr(client, "_get_session", get_session)

    with pytest.raises(NonRetryableHttpError) as exc_info:
        await client.fetch_html("/missing")

    assert exc_info.value.status == 404
    assert client.stats == {"requests": 1, "failures": 1, "retries": 0}
