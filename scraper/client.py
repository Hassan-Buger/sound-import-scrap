import asyncio
import json
import logging
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app.config import settings

logger = logging.getLogger("scraper.client")


class HttpClientError(Exception):
    pass


class HttpClient:
    """Async HTTP client with retry, rate limiting, and concurrency control."""

    def __init__(
        self,
        base_url: str = settings.base_url,
        concurrency: int = settings.concurrency,
        request_delay: float = settings.request_delay,
        max_retries: int = settings.max_retries,
        timeout: int = settings.request_timeout,
        user_agent: str = settings.user_agent,
    ):
        self.base_url = base_url
        self.max_retries = max_retries
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.user_agent = user_agent
        self.semaphore = asyncio.Semaphore(concurrency)
        self.request_delay = request_delay
        self._last_request = 0.0
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {"requests": 0, "failures": 0, "retries": 0}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                connector=aiohttp.TCPConnector(ssl=False, limit=0),
            )
        return self._session

    def _rate_limit(self):
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request
        if elapsed < self.request_delay:
            return self.request_delay - elapsed
        return 0

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    def _build_retry_decorator(self):
        return retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type(
                (aiohttp.ClientError, asyncio.TimeoutError, HttpClientError)
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    async def fetch_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        full_url = self._build_url(url)

        async def _do_fetch():
            async with self.semaphore:
                delay = self._rate_limit()
                if delay > 0:
                    await asyncio.sleep(delay)

                session = await self._get_session()
                self._last_request = asyncio.get_event_loop().time()

                logger.debug("Fetching %s", full_url)
                async with session.get(full_url, params=params) as resp:
                    self._stats["requests"] += 1
                    if resp.status != 200:
                        text = await resp.text()
                        raise HttpClientError(
                            f"HTTP {resp.status} for {full_url}: {text[:200]}"
                        )
                    content_type = resp.content_type
                    if "json" in content_type:
                        return await resp.json()
                    text = await resp.text()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        raise HttpClientError(
                            f"Non-JSON response from {full_url}: {text[:200]}"
                        )

        if retry:
            retry_decorator = self._build_retry_decorator()
            try:
                return await retry_decorator(_do_fetch)()
            except Exception as e:
                self._stats["failures"] += 1
                logger.error("Failed to fetch %s after %d retries: %s", full_url, self.max_retries, e)
                raise
        else:
            return await _do_fetch()

    async def fetch_html(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> str:
        full_url = self._build_url(url)

        async def _do_fetch():
            async with self.semaphore:
                delay = self._rate_limit()
                if delay > 0:
                    await asyncio.sleep(delay)

                session = await self._get_session()
                self._last_request = asyncio.get_event_loop().time()

                logger.debug("Fetching HTML %s", full_url)
                async with session.get(full_url, params=params) as resp:
                    self._stats["requests"] += 1
                    if resp.status != 200:
                        raise HttpClientError(
                            f"HTTP {resp.status} for {full_url}"
                        )
                    return await resp.text()

        if retry:
            retry_decorator = self._build_retry_decorator()
            try:
                return await retry_decorator(_do_fetch)()
            except Exception as e:
                self._stats["failures"] += 1
                logger.error("Failed to fetch HTML %s: %s", full_url, e)
                raise
        else:
            return await _do_fetch()

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
