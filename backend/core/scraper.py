"""Search the public web and download HTML/PDF sources for a topic."""

from __future__ import annotations

import contextvars
import ipaddress
import hashlib
import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeVar
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
import tldextract
from bs4 import BeautifulSoup

from backend.observability.logging import safe_exception_context
from . import config
from .snapshot_paths import write_bytes_atomic

try:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException
except ImportError:  # pragma: no cover
    from duckduckgo_search import DDGS
    from duckduckgo_search.exceptions import DuckDuckGoSearchException as DDGSException


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    content_type: str
    text: str
    local_path: Path | None = None


@dataclass(frozen=True)
class _FetchTarget:
    host: str
    port: int
    address: str


class SearchProviderUnavailableError(RuntimeError):
    """Raised when every attempted web-search query fails at the provider layer."""

    def __init__(self, *, query_count: int, failed_query_count: int) -> None:
        super().__init__("No usable web-search results were available because provider queries failed.")
        self.query_count = query_count
        self.failed_query_count = failed_query_count


_PDF_NAME = re.compile(r"[^A-Za-z0-9._-]+")
logger = logging.getLogger(__name__)
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")
_TLD_EXTRACTOR = tldextract.TLDExtract(
    suffix_list_urls=(),
    cache_dir=None,
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not getattr(address, "is_site_local", False)
    )


def is_public_ip(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject non-public addresses, including IPv4 hidden in IPv6 forms."""
    try:
        address = ipaddress.ip_address(ip)
    except (TypeError, ValueError):
        return False

    if isinstance(address, ipaddress.IPv4Address):
        return _address_is_public(address)
    if address.scope_id is not None:
        return False

    mapped = address.ipv4_mapped
    if mapped is not None:
        return _address_is_public(mapped)

    six_to_four = address.sixtofour
    if six_to_four is not None:
        return _address_is_public(six_to_four) and _address_is_public(address)

    teredo = address.teredo
    if teredo is not None:
        server, client = teredo
        return _address_is_public(server) and _address_is_public(client) and _address_is_public(address)

    if address in _NAT64_WELL_KNOWN:
        # ipaddress marks this translation prefix as reserved on Python 3.11,
        # so validate the actual destination IPv4 address instead.
        return _address_is_public(ipaddress.IPv4Address(address.packed[12:]))

    if address in _NAT64_LOCAL_USE:
        # RFC 6052 /48 layout: 48 prefix bits, 16 IPv4 bits, a zero u byte,
        # 16 more IPv4 bits, and a zero suffix.
        packed = address.packed
        if packed[8] != 0 or any(packed[11:]):
            return False
        return _address_is_public(ipaddress.IPv4Address(packed[6:8] + packed[9:11]))

    return _address_is_public(address)


def _resolve_public_http_url(url: str) -> _FetchTarget | None:
    """Resolve once, reject every unsafe answer, and retain one pinned IP."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if parsed.scheme not in {"http", "https"} or port not in {80, 443}:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if not host or host.lower() in {"localhost", "127.0.0.1", "::1"}:
        return None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return _FetchTarget(host.lower(), port, str(literal)) if is_public_ip(literal) else None
    try:
        normalized_host = host.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError:
        return None
    if not normalized_host:
        return None
    try:
        infos = socket.getaddrinfo(normalized_host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos:
        return None
    addresses: list[str] = []
    for info in infos:
        try:
            address = info[4][0]
        except (IndexError, TypeError):
            return None
        if not is_public_ip(address):
            return None
        addresses.append(str(ipaddress.ip_address(address)))
    return _FetchTarget(normalized_host, port, addresses[0])


def _is_public_http_url(url: str) -> bool:
    return _resolve_public_http_url(url) is not None


def _looks_like_http_url(url: str) -> bool:
    """Cheap syntactic filter; fetch_url still resolves and pins before connecting."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _url_looks_like_pdf(url: str) -> bool:
    try:
        return urlparse(url).path.lower().endswith(".pdf")
    except ValueError:
        return False


def search_web(query: str, max_results: int = 8) -> list[SearchHit]:
    hits: list[SearchHit] = []
    with DDGS() as ddgs:
        for row in ddgs.text(query, max_results=max_results) or []:
            url = (row.get("href") or row.get("url") or "").strip()
            if not url or not _looks_like_http_url(url):
                continue
            hits.append(
                SearchHit(
                    title=(row.get("title") or "").strip(),
                    url=url,
                    snippet=(row.get("body") or row.get("snippet") or "").strip(),
                )
            )
    return hits


def _search_failure_details(exc: Exception) -> tuple[str, int | None, str | None]:
    """Reduce a DDGS exception to safe, useful diagnostic fields."""
    message = str(exc)
    lowered = message.lower()
    status_match = re.search(
        r"\bHTTP(?:/\d(?:\.\d)?)?\s+([45]\d{2})\b|"
        r"\bstatus(?:\s+code)?\s*[:= ]\s*([45]\d{2})\b|"
        r"\b(401|403|408|429|500|502|503|504)\b",
        message,
        flags=re.IGNORECASE,
    )
    status_code = next((int(value) for value in status_match.groups() if value), None) if status_match else None

    provider_host = None
    for candidate in re.findall(r"https?://[^\s'\"<>]+", message, flags=re.IGNORECASE):
        host = urlparse(candidate.rstrip(".,);]")).hostname
        if host and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            provider_host = host.lower()
            break

    if "timeout" in type(exc).__name__.lower() or "timed out" in lowered:
        reason = "timeout"
    elif status_code == 429:
        reason = "rate_limited"
    elif status_code in {401, 403}:
        reason = "blocked_or_forbidden"
    elif status_code is not None and status_code >= 500:
        reason = "upstream_server_error"
    elif status_code is not None:
        reason = "upstream_http_error"
    elif "no results found" in lowered:
        reason = "no_usable_results"
    else:
        reason = "metasearch_error"
    return reason, status_code, provider_host


def search_topic_sources(
    topic: str,
    queries: Iterable[str] | None = None,
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> list[SearchHit]:
    """Execute a bounded query plan and deduplicate public search results."""
    if queries is None:
        query_list = [
            f"{topic} filetype:pdf",
            f"{topic} official report filetype:pdf",
            f"{topic} site:arxiv.org filetype:pdf",
            f"{topic}",
        ]
    else:
        query_list = list(
            dict.fromkeys(
                query.strip()[:250]
                for query in queries
                if isinstance(query, str) and query.strip()
            )
        )[:6]
    if not query_list:
        query_list = [topic]

    logger.info("Executing web search plan (query_count=%s)", len(query_list))
    seen: set[str] = set()
    hits: list[SearchHit] = []
    failed_query_count = 0
    for query_index, query in enumerate(query_list, start=1):
        query_started = time.perf_counter()
        if on_progress:
            on_progress(
                {
                    "step": "search",
                    "status": "active",
                    "message": f"Searching query {query_index} of {len(query_list)}: {query[:160]}",
                    "current": query_index,
                    "total": len(query_list),
                }
            )
        logger.info("Executing web search query (query_index=%s/%s)", query_index, len(query_list))
        try:
            query_hits = search_web(query, max_results=8)
        except DDGSException as exc:
            failed_query_count += 1
            reason, status_code, provider_host = _search_failure_details(exc)
            if on_progress:
                on_progress(
                    {
                        "step": "search",
                        "status": "active",
                        "message": f"Query {query_index} failed ({reason}); trying the remaining searches.",
                        "current": query_index,
                        "total": len(query_list),
                    }
                )
            logger.warning(
                "Web search query failed (query_index=%s/%s query_hash=%s reason=%s "
                "error_type=%s provider_host=%s http_status=%s duration_ms=%.1f fallback=continue)",
                query_index,
                len(query_list),
                hashlib.sha256(query.encode("utf-8")).hexdigest()[:12],
                reason,
                type(exc).__name__,
                provider_host or "unknown",
                status_code or "unknown",
                (time.perf_counter() - query_started) * 1000,
            )
            continue

        logger.info(
            "Web search query completed (query_index=%s/%s result_count=%s duration_ms=%.1f)",
            query_index,
            len(query_list),
            len(query_hits),
            (time.perf_counter() - query_started) * 1000,
        )
        if on_progress:
            on_progress(
                {
                    "step": "search",
                    "status": "active",
                    "message": f"Query {query_index} returned {len(query_hits)} results.",
                    "current": query_index,
                    "total": len(query_list),
                }
            )
        for hit in query_hits:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            hits.append(hit)

    logger.info(
        "Web search plan completed (query_count=%s failed_query_count=%s unique_result_count=%s)",
        len(query_list),
        failed_query_count,
        len(hits),
    )
    if failed_query_count and not hits:
        raise SearchProviderUnavailableError(
            query_count=len(query_list),
            failed_query_count=failed_query_count,
        )
    return hits


def _safe_filename(url: str) -> str:
    name = Path(urlparse(url).path).name or "source.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return _PDF_NAME.sub("_", name)[:120]


def source_origin(url: str) -> str:
    """Return the full hostname for display and source-level diagnostics."""
    return (urlparse(url).hostname or "").lower()


def registrable_origin(url: str) -> str:
    """Return an offline PSL-based key for independent-source counting."""
    host = source_origin(url).rstrip(".")
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    result = _TLD_EXTRACTOR(ascii_host)
    if result.domain and result.suffix:
        return f"{result.domain}.{result.suffix}".lower()
    # Unknown suffixes must not let sibling subdomains self-corroborate.
    return (result.domain or ascii_host).lower()


def _remaining_seconds(deadline: float, timeout: float | None = None) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Web fetch exceeded its total deadline.")
    return min(remaining, timeout) if timeout is not None else remaining


class _SocketStream(httpcore.NetworkStream):
    """Adapt an already-connected numeric-IP socket to httpcore."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self._sock.settimeout(timeout)
        return self._sock.recv(max_bytes)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        while buffer:
            self._sock.settimeout(timeout)
            sent = self._sock.send(buffer)
            if sent == 0:
                raise OSError("Web fetch socket closed while writing.")
            buffer = buffer[sent:]

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        try:
            self._sock.settimeout(timeout)
            wrapped = ssl_context.wrap_socket(self._sock, server_hostname=server_hostname)
        except BaseException:
            self.close()
            raise
        return _SocketStream(wrapped)

    def get_extra_info(self, info: str):
        if info == "socket":
            return self._sock
        if info == "server_addr":
            return self._sock.getpeername()
        if info == "client_addr":
            return self._sock.getsockname()
        return None

    def close(self) -> None:
        self._sock.close()


class _DeadlineStream(httpcore.NetworkStream):
    """Clamp every socket operation to the same end-to-end fetch deadline."""

    def __init__(self, stream: httpcore.NetworkStream, deadline: float) -> None:
        self._stream = stream
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout=_remaining_seconds(self._deadline, timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout=_remaining_seconds(self._deadline, timeout))

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        stream = self._stream.start_tls(
            ssl_context,
            server_hostname=server_hostname,
            timeout=_remaining_seconds(self._deadline, timeout),
        )
        return _DeadlineStream(stream, self._deadline)

    def get_extra_info(self, info: str):
        return self._stream.get_extra_info(info)

    def close(self) -> None:
        self._stream.close()


class _PinnedBackend(httpcore.SyncBackend):
    """Connect a validated hostname directly to its chosen numeric address."""

    def __init__(self, target: _FetchTarget, deadline: float) -> None:
        self._target = target
        self._deadline = deadline

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        if host.rstrip(".").lower() != self._target.host or port != self._target.port:
            raise ValueError("Blocked connection to an unvalidated destination.")
        if local_address is not None:
            raise ValueError("Local binding is not supported for source fetches.")

        address = ipaddress.ip_address(self._target.address)
        family = socket.AF_INET6 if isinstance(address, ipaddress.IPv6Address) else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(_remaining_seconds(self._deadline, timeout))
            if socket_options:
                for option in socket_options:
                    sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # A numeric socket destination never asks DNS to resolve the host again.
            sock.connect((self._target.address, port))
            return _DeadlineStream(_SocketStream(sock), self._deadline)
        except BaseException:
            sock.close()
            raise

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise ValueError("Unix sockets are not valid web-fetch destinations.")


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    def __iter__(self):
        yield from self._stream

    def close(self) -> None:
        self._stream.close()


class _PinnedTransport(httpx.BaseTransport):
    """Use httpcore's original URL for Host/SNI while pinning its TCP peer."""

    def __init__(self, target: _FetchTarget, deadline: float) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_PinnedBackend(target, deadline),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = self._pool.handle_request(core_request)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()


def _client(target: _FetchTarget, deadline: float) -> httpx.Client:
    return httpx.Client(
        follow_redirects=False,
        timeout=config.FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": config.USER_AGENT, "Accept": "*/*"},
        trust_env=False,
        transport=_PinnedTransport(target, deadline),
    )


def fetch_url(url: str) -> tuple[str, bytes, str]:
    deadline = time.monotonic() + config.FETCH_TIMEOUT_SECONDS
    current_url = url
    for _ in range(6):
        _remaining_seconds(deadline)
        target = _resolve_public_http_url(current_url)
        if target is None:
            raise ValueError("Blocked non-public web-fetch URL or redirect.")
        with _client(target, deadline) as client:
            with client.stream("GET", current_url) as response:
                _remaining_seconds(deadline)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response did not include a Location header.")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                # Only the first MAX_HTML_BYTES of a page are ever read, so stop
                # downloading there instead of pulling up to the PDF ceiling.
                truncate = "html" in content_type
                limit = config.MAX_HTML_BYTES if truncate else config.MAX_PDF_BYTES
                content_length = response.headers.get("content-length")
                if not truncate and content_length and int(content_length) > limit:
                    raise ValueError(f"Response exceeds size limit: {current_url}")

                body = bytearray()
                for chunk in response.iter_bytes():
                    _remaining_seconds(deadline)
                    body.extend(chunk)
                    if len(body) > limit:
                        if truncate:
                            del body[limit:]
                            break
                        raise ValueError(f"Response exceeds size limit: {current_url}")
                return current_url, bytes(body), content_type
    raise ValueError("Too many redirects while fetching URL.")


def looks_like_pdf(url: str, content_type: str, body: bytes) -> bool:
    if "pdf" in content_type:
        return True
    if urlparse(url).path.lower().endswith(".pdf"):
        return True
    return body.startswith(b"%PDF")


def _snapshot_name(url: str, body: bytes, suffix: str) -> str:
    stem = _PDF_NAME.sub("_", Path(urlparse(url).path).stem or "source")[:80] or "source"
    return f"{stem}-{hashlib.sha256(body).hexdigest()[:16]}{suffix}"


def save_pdf(url: str, body: bytes) -> Path:
    """Store a PDF under a content-addressed name; identical bytes share one file."""
    if len(body) > config.MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds size limit: {url}")
    path = config.WEB_PDF_DIR / _snapshot_name(url, body, ".pdf")
    # Content-addressed files are never rewritten, so earlier evidence keeps
    # pointing at exactly the bytes it was verified against.
    if not path.exists():
        write_bytes_atomic(path, body)
    return path


def save_html(url: str, body: bytes) -> Path:
    """Save the exact HTML snapshot used for an evidence check."""
    if len(body) > config.MAX_HTML_BYTES:
        body = body[: config.MAX_HTML_BYTES]
    path = config.WEB_HTML_DIR / _snapshot_name(url, body, ".html")
    if not path.exists():
        write_bytes_atomic(path, body)
    return path


def extract_html_text(
    html: bytes, url: str, *, collect_pdf_links: bool = True
) -> tuple[str, str, list[str]]:
    if len(html) > config.MAX_HTML_BYTES:
        html = html[: config.MAX_HTML_BYTES]
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""
    pdf_links: list[str] = []
    if collect_pdf_links:
        for tag in soup.find_all("a", href=True):
            href = urljoin(url, tag["href"])
            # No DNS here: fetch_url validates and pins each link before use.
            if _url_looks_like_pdf(href) and _looks_like_http_url(href):
                pdf_links.append(href)
    for tag in soup(["script", "style", "noscript", "nav", "footer", "form"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return title, text, pdf_links


class FetchBudget:
    """Total bytes one research run may download, shared across its fetches."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = config.RESEARCH_MAX_FETCH_BYTES if limit is None else limit
        self._remaining = self.limit
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self.limit - self._remaining

    def consume(self, size: int) -> None:
        with self._lock:
            self._remaining -= size

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._remaining <= 0


_host_slots: dict[str, threading.BoundedSemaphore] = {}
_host_slots_lock = threading.Lock()


@contextmanager
def _host_slot(url: str) -> Iterator[None]:
    """Limit simultaneous requests to one host."""
    host = source_origin(url)
    with _host_slots_lock:
        slot = _host_slots.setdefault(
            host, threading.BoundedSemaphore(config.FETCH_PER_HOST_CONCURRENCY)
        )
    with slot:
        yield


ItemT = TypeVar("ItemT")
_SKIPPED = ("skipped", None)


def _guarded(function: Callable[[ItemT], object], item: ItemT) -> tuple[str, object]:
    try:
        return "ok", function(item)
    except Exception as exc:
        return "error", exc


def _ordered_fetches(
    items: Iterable[ItemT],
    fetch: Callable[[ItemT], object],
    *,
    should_fetch: Callable[[ItemT], bool],
    check_cancelled: Callable[[], None] | None = None,
) -> Iterator[tuple[ItemT, tuple[str, object]]]:
    """Fetch up to FETCH_CONCURRENCY items ahead, yielding outcomes in input order.

    `should_fetch` is evaluated when an item is scheduled, so caps apply with
    at most one window of look-ahead.
    """
    remaining = iter(items)
    window: deque[tuple[ItemT, Future | None]] = deque()
    pool = ThreadPoolExecutor(max_workers=config.FETCH_CONCURRENCY, thread_name_prefix="fetch")

    def fill() -> None:
        while len(window) < config.FETCH_CONCURRENCY:
            try:
                item = next(remaining)
            except StopIteration:
                return
            if check_cancelled:
                check_cancelled()
            if not should_fetch(item):
                window.append((item, None))
                continue
            # Each task gets its own context so logs keep the request ID.
            context = contextvars.copy_context()
            window.append((item, pool.submit(context.run, _guarded, fetch, item)))

    try:
        fill()
        while window:
            item, future = window.popleft()
            outcome = _SKIPPED if future is None else future.result()
            yield item, outcome
            if check_cancelled:
                check_cancelled()
            fill()
    finally:
        for _, future in window:
            if future is not None:
                future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def download_reference_pdfs(
    urls: Iterable[str],
    limit: int | None = None,
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    budget: FetchBudget | None = None,
    prefetched: dict[str, tuple[str, bytes, str]] | None = None,
) -> list[FetchedPage]:
    """Download a bounded set of PDFs, reusing bodies fetched while collecting."""
    limit = config.MAX_WEB_PDFS if limit is None else limit
    budget = budget or FetchBudget()
    prefetched = prefetched or {}
    pages: list[FetchedPage] = []
    url_list = list(dict.fromkeys(urls))

    def fetch(url: str) -> tuple[str, bytes, str]:
        if url in prefetched:
            return prefetched[url]
        with _host_slot(url):
            return fetch_url(url)

    def should_fetch(url: str) -> bool:
        return len(pages) < limit and (url in prefetched or not budget.exhausted)

    for index, (url, (status, value)) in enumerate(
        _ordered_fetches(url_list, fetch, should_fetch=should_fetch, check_cancelled=check_cancelled),
        start=1,
    ):
        if len(pages) >= limit:
            break
        if on_progress:
            on_progress(
                {
                    "step": "collect",
                    "status": "active",
                    "message": f"Checking PDF {index} of {len(url_list)} ({source_origin(url)}).",
                    "current": index,
                    "total": len(url_list),
                }
            )
        if status == "skipped":
            continue
        try:
            if status == "error":
                raise value  # type: ignore[misc]
            final_url, body, content_type = value  # type: ignore[misc]
            if url not in prefetched:
                budget.consume(len(body))
            if not looks_like_pdf(final_url, content_type, body):
                continue
            path = save_pdf(final_url, body)
            pages.append(
                FetchedPage(
                    url=final_url,
                    title=path.name,
                    content_type="application/pdf",
                    text="",
                    local_path=path,
                )
            )
        except Exception as exc:
            logger.warning(
                "Reference PDF download failed; continuing with other sources (host=%s %s)",
                source_origin(url),
                safe_exception_context(exc),
            )
            continue
    logger.info("Reference PDF collection completed (downloaded=%s limit=%s)", len(pages), limit)
    return pages


def collect_research_sources(
    hits: Iterable[SearchHit],
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    budget: FetchBudget | None = None,
    prefetched: dict[str, tuple[str, bytes, str]] | None = None,
) -> tuple[list[FetchedPage], list[str]]:
    """Fetch result pages once and return HTML snapshots plus linked PDF URLs.

    PDF links are not downloaded here; PDF bodies discovered by content type
    are put in `prefetched` so download_reference_pdfs does not fetch them twice.
    """
    budget = budget or FetchBudget()
    prefetched = prefetched if prefetched is not None else {}
    html_pages: list[FetchedPage] = []
    pdf_urls: list[str] = []
    hit_list: list[SearchHit] = []
    seen_urls: set[str] = set()
    for hit in hits:
        if hit.url not in seen_urls:
            seen_urls.add(hit.url)
            hit_list.append(hit)

    def fetch(hit: SearchHit) -> tuple[str, bytes, str]:
        with _host_slot(hit.url):
            return fetch_url(hit.url)

    def should_fetch(hit: SearchHit) -> bool:
        if _url_looks_like_pdf(hit.url) or budget.exhausted:
            return False
        return len(html_pages) < config.MAX_WEB_HTML

    for index, (hit, (status, value)) in enumerate(
        _ordered_fetches(hit_list, fetch, should_fetch=should_fetch, check_cancelled=check_cancelled),
        start=1,
    ):
        if on_progress:
            on_progress(
                {
                    "step": "collect",
                    "status": "active",
                    "message": f"Fetching source {index} of {len(hit_list)} ({source_origin(hit.url)}).",
                    "current": index,
                    "total": len(hit_list),
                }
            )
        if status == "skipped":
            if _url_looks_like_pdf(hit.url):
                pdf_urls.append(hit.url)
            continue
        if status == "error":
            logger.warning(
                "Research source fetch failed; continuing with other results (host=%s %s)",
                source_origin(hit.url),
                safe_exception_context(value),  # type: ignore[arg-type]
            )
            continue
        final_url, body, content_type = value  # type: ignore[misc]
        budget.consume(len(body))
        if looks_like_pdf(final_url, content_type, body):
            prefetched[final_url] = (final_url, body, content_type)
            pdf_urls.append(final_url)
            continue
        if len(html_pages) >= config.MAX_WEB_HTML:
            continue
        if "html" not in content_type and not body.lstrip().startswith(b"<"):
            continue
        title, text, nested_pdfs = extract_html_text(body, final_url)
        if len(text) >= 200:
            path = save_html(final_url, body)
            html_pages.append(
                FetchedPage(
                    url=final_url,
                    title=title or hit.title or path.name,
                    content_type="text/html",
                    text=text,
                    local_path=path,
                )
            )
        pdf_urls.extend(nested_pdfs)

    unique_pdfs = list(dict.fromkeys(pdf_urls))
    logger.info(
        "Web source collection completed (html_sources=%s pdf_candidates=%s)",
        len(html_pages),
        len(unique_pdfs),
    )
    return html_pages, unique_pdfs
