import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import aiohttp
from app.config import settings

logger = logging.getLogger("scraper.client")


class HttpClientError(Exception):
    pass


class RetryableHttpError(HttpClientError):
    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NonRetryableHttpError(HttpClientError):
    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


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
        self.max_retries = max(1, max_retries)
        self.concurrency = max(1, concurrency)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.user_agent = user_agent
        self.semaphore = asyncio.Semaphore(self.concurrency)
        rate_delay = 1.0 / settings.rate_limit if settings.rate_limit > 0 else 0.0
        self.request_delay = max(0.0, request_delay, rate_delay)
        self._next_request_at = 0.0
        self._rate_lock = asyncio.Lock()
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {"requests": 0, "failures": 0, "retries": 0}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                connector=aiohttp.TCPConnector(limit=self.concurrency),
            )
        return self._session

    async def _wait_for_rate_limit(self) -> None:
        if self.request_delay <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_request_at = loop.time() + self.request_delay

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    @staticmethod
    def _retry_after_seconds(raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    async def _request(
        self,
        url: str,
        params: Optional[Dict[str, Any]],
        *,
        expect_json: bool,
        retry_enabled: bool,
    ):
        full_url = self._build_url(url)
        attempts = self.max_retries if retry_enabled else 1

        for attempt in range(1, attempts + 1):
            try:
                async with self.semaphore:
                    await self._wait_for_rate_limit()
                    session = await self._get_session()
                    logger.debug("Fetching %s", full_url)
                    async with session.get(full_url, params=params) as resp:
                        self._stats["requests"] += 1
                        if resp.status != 200:
                            body = (await resp.text())[:200]
                            message = f"HTTP {resp.status} for {full_url}: {body}"
                            if resp.status in {408, 425, 429, 500, 502, 503, 504}:
                                raise RetryableHttpError(
                                    message,
                                    status=resp.status,
                                    retry_after=self._retry_after_seconds(
                                        resp.headers.get("Retry-After")
                                    ),
                                )
                            raise NonRetryableHttpError(message, status=resp.status)

                        if not expect_json:
                            return await resp.text()
                        text = await resp.text()
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError as exc:
                            raise RetryableHttpError(
                                f"Invalid JSON from {full_url}: {text[:200]}"
                            ) from exc

            except NonRetryableHttpError:
                self._stats["failures"] += 1
                raise
            except RetryableHttpError as exc:
                retry_exc = exc
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                retry_exc = RetryableHttpError(
                    f"{type(exc).__name__} for {full_url}: {exc}"
                )

            if attempt >= attempts:
                self._stats["failures"] += 1
                logger.error(
                    "Failed to fetch %s after %d attempt(s): %s",
                    full_url,
                    attempts,
                    retry_exc,
                )
                raise retry_exc

            self._stats["retries"] += 1
            retry_after = getattr(retry_exc, "retry_after", None)
            wait = retry_after if retry_after is not None else min(30.0, 2 ** (attempt - 1))
            wait += random.uniform(0.0, min(0.5, wait * 0.1))
            logger.warning(
                "Request attempt %d/%d failed for %s (%s); retrying in %.2fs",
                attempt,
                attempts,
                full_url,
                retry_exc,
                wait,
            )
            await asyncio.sleep(wait)

    async def fetch_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Dict[str, Any]:
        return await self._request(
            url, params, expect_json=True, retry_enabled=retry
        )

    async def fetch_html(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> str:
        return await self._request(
            url, params, expect_json=False, retry_enabled=retry
        )

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
