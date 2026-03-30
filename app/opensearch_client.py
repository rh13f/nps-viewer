"""OpenSearch client with search_after pagination."""
from __future__ import annotations
import logging
from typing import Any, Generator, Optional

from opensearchpy import OpenSearch, ConnectionError as OSConnectionError, TransportError

from app.config import Config

logger = logging.getLogger(__name__)


def build_client(cfg: Config) -> OpenSearch:
    """Create an OpenSearch client from config."""
    kwargs: dict[str, Any] = {
        "hosts": [{"host": cfg.opensearch.host, "port": cfg.opensearch.port}],
        "use_ssl": cfg.opensearch.use_ssl,
        "verify_certs": cfg.opensearch.verify_certs,
        "ssl_show_warn": False,
    }
    if cfg.opensearch.ca_certs:
        kwargs["ca_certs"] = cfg.opensearch.ca_certs
    if cfg.opensearch.username:
        kwargs["http_auth"] = (cfg.opensearch.username, cfg.opensearch.password)
    return OpenSearch(**kwargs)


def is_reachable(client: OpenSearch) -> bool:
    """Return True if OpenSearch responds to a ping."""
    try:
        return client.ping()
    except (OSConnectionError, TransportError):
        return False


def search_after_pages(
    client: OpenSearch,
    index: str,
    query: dict,
    sort: list,
    page_size: int = 1000,
    max_hits: int = 10_000,
) -> Generator[list[dict], None, None]:
    """Yield pages of hits using search_after pagination.

    Stops when all results are returned or max_hits is reached.
    """
    fetched = 0
    search_after: Optional[list] = None

    while fetched < max_hits:
        body: dict[str, Any] = {
            "query": query,
            "sort": sort,
            "size": min(page_size, max_hits - fetched),
        }
        if search_after is not None:
            body["search_after"] = search_after

        try:
            resp = client.search(index=index, body=body)
        except Exception as exc:
            logger.error("OpenSearch search failed: %s", exc)
            raise

        hits = resp["hits"]["hits"]
        if not hits:
            break

        yield hits
        fetched += len(hits)

        if len(hits) < page_size:
            break

        search_after = hits[-1]["sort"]


def fetch_all(
    client: OpenSearch,
    index: str,
    query: dict,
    sort: list,
    max_hits: int = 10_000,
) -> list[dict]:
    """Fetch all matching documents up to max_hits."""
    results = []
    for page in search_after_pages(client, index, query, sort, max_hits=max_hits):
        results.extend(page)
    return results


def count_hits(client: OpenSearch, index: str, query: dict) -> int:
    """Return the total hit count for a query (fast, no docs returned)."""
    try:
        resp = client.count(index=index, body={"query": query})
        return resp["count"]
    except Exception as exc:
        logger.error("OpenSearch count failed: %s", exc)
        raise
