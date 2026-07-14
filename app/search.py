"""Compliant public-search providers and deterministic metadata extraction."""

import math
import os
import re
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

import httpx

from app.profiles import upsert_profile

SERPER_URL = "https://google.serper.dev/search"
LINKEDIN_SUFFIX = re.compile(r"\s*\|\s*LinkedIn\s*$", re.IGNORECASE)
TITLE_SEPARATOR = re.compile(r"\s+(?:-|–|—)\s+")
ROLE_AT_COMPANY = re.compile(r"^(?P<role>.+?)\s+at\s+(?P<company>.+)$", re.IGNORECASE)


class SearchProviderError(RuntimeError):
    """A search provider could not return usable public result metadata."""


class SearchConfigurationError(SearchProviderError):
    """A search provider is missing required configuration."""


class SearchProvider(Protocol):
    """Interface implemented by public search metadata providers."""

    name: str

    def search(self, query: str) -> list[dict[str, str]]:
        """Return public result metadata without fetching result URLs."""


def _normalized_phrase(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    # Quotes and backslashes are search syntax. Treat them as word boundaries so
    # user-provided names remain inside one generated quoted phrase.
    normalized = " ".join(value.replace('"', " ").replace("\\", " ").split())
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def build_queries(company: str, roles: Iterable[str] | None = None) -> list[str]:
    """Build a base LinkedIn public-profile query and unique role refinements."""
    normalized_company = _normalized_phrase(company, "company")
    base = f'site:linkedin.com/in "{normalized_company}"'
    queries = [base]
    seen: set[str] = set()
    for role in roles or ():
        if not isinstance(role, str):
            raise ValueError("role must be a string")
        if not role.strip():
            continue
        normalized_role = _normalized_phrase(role, "role")
        key = normalized_role.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append(f'{base} "{normalized_role}"')
    return queries


def _same_text(first: str, second: str) -> bool:
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def extract_result(
    result: Mapping[str, Any], company: str
) -> dict[str, str | None]:
    """Extract only deterministic identity fields from public result metadata."""
    normalized_company = _normalized_phrase(company, "company")
    raw_title = result.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    name: str | None = None
    role: str | None = None
    current_company: str | None = None

    suffix = LINKEDIN_SUFFIX.search(title)
    if suffix:
        body = title[: suffix.start()].strip()
        parts = [part.strip() for part in TITLE_SEPARATOR.split(body) if part.strip()]
        if parts:
            name = parts[0]
        if len(parts) >= 3 and _same_text(parts[-1], normalized_company):
            role = " - ".join(parts[1:-1])
            current_company = normalized_company
        elif len(parts) == 2:
            match = ROLE_AT_COMPANY.fullmatch(parts[1])
            if match and _same_text(match.group("company"), normalized_company):
                role = match.group("role").strip()
                current_company = normalized_company

    raw_url = result.get("url", result.get("link"))
    profile_url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
    raw_query = result.get("query")
    source_query = (
        raw_query.strip()
        if isinstance(raw_query, str) and raw_query.strip()
        else None
    )
    return {
        "name": name,
        "role": role,
        "current_company": current_company,
        "profile_url": profile_url,
        "source": "search",
        "source_query": source_query,
        "review_status": "pending",
    }


class DemoSearchProvider:
    """Return deterministic, explicitly fictional public-result metadata."""

    name = "demo"

    def search(self, query: str) -> list[dict[str, str]]:
        phrases = re.findall(r'"([^"]+)"', query)
        company = phrases[0] if phrases else "Example Company"
        requested_role = phrases[1] if len(phrases) > 1 else "Research Engineer"
        return [
            {
                "title": (
                    f"Avery Chen - {requested_role} - {company} | LinkedIn"
                ),
                "url": (
                    "https://www.linkedin.com/in/"
                    "fictional-demo-avery-chen-000000"
                ),
                "snippet": "Fictional demonstration search result.",
                "query": query,
            },
            {
                "title": f"Jordan Rivera - Talent Researcher - {company} | LinkedIn",
                "url": (
                    "https://www.linkedin.com/in/"
                    "fictional-demo-jordan-rivera-000000"
                ),
                "snippet": "Fictional demonstration search result.",
                "query": query,
            },
        ]


class SerperSearchProvider:
    """Search Serper's public index endpoint without opening result URLs."""

    name = "serper"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("SERPER_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise SearchConfigurationError(
                "Serper search requires SERPER_API_KEY or an explicit api_key"
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        self._api_key = key.strip()
        self._timeout = float(timeout)
        self._transport = transport

    def search(self, query: str) -> list[dict[str, str]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = client.post(
                    SERPER_URL,
                    headers={
                        "X-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": query},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise SearchProviderError(
                f"Serper HTTP request failed with status "
                f"{error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise SearchProviderError(f"Serper HTTP request failed: {error}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise SearchProviderError("Serper returned invalid JSON") from error
        if not isinstance(payload, Mapping):
            raise SearchProviderError("Serper returned an invalid JSON payload")
        organic = payload.get("organic", [])
        if not isinstance(organic, list):
            raise SearchProviderError("Serper JSON field 'organic' must be a list")

        mapped: list[dict[str, str]] = []
        for item in organic:
            if not isinstance(item, Mapping):
                raise SearchProviderError(
                    "Serper returned an invalid organic result"
                )
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            if not all(isinstance(value, str) for value in (title, link, snippet)):
                raise SearchProviderError(
                    "Serper returned an invalid organic result"
                )
            mapped.append(
                {
                    "title": title,
                    "url": link,
                    "snippet": snippet,
                    "query": query,
                }
            )
        return mapped


def _provider_name(provider: SearchProvider) -> str:
    value = getattr(provider, "name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return provider.__class__.__name__


def _mark_failed(
    conn: sqlite3.Connection, run_id: int, error: Exception
) -> None:
    message = str(error).strip() or error.__class__.__name__
    conn.execute(
        """
        UPDATE search_runs
        SET status = 'failed', result_count = 0, error_message = ?
        WHERE id = ?
        """,
        (message[:2000], run_id),
    )


def _run_row(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM search_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("search run not found")
    return dict(row)


def run_search(
    conn: sqlite3.Connection,
    project_id: int,
    provider: SearchProvider,
    roles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run all project queries and atomically upsert their pending profiles."""
    project = conn.execute(
        "SELECT company FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None:
        raise LookupError("project not found")

    queries = build_queries(project["company"], roles)
    provider_name = _provider_name(provider)
    run_id = conn.execute(
        """
        INSERT INTO search_runs (project_id, query, provider, status)
        VALUES (?, ?, ?, 'running')
        """,
        (project_id, "\n".join(queries), provider_name),
    ).lastrowid
    if run_id is None:
        raise RuntimeError("failed to create search run")

    results: list[tuple[str, Mapping[str, Any]]] = []
    try:
        for query in queries:
            provider_results = provider.search(query)
            if isinstance(provider_results, (str, bytes)) or not isinstance(
                provider_results, Iterable
            ):
                raise SearchProviderError(
                    f"{provider_name} returned an invalid result collection"
                )
            for result in provider_results:
                if not isinstance(result, Mapping):
                    raise SearchProviderError(
                        f"{provider_name} returned an invalid search result"
                    )
                results.append((query, result))
    except Exception as error:
        _mark_failed(conn, run_id, error)
        if isinstance(error, SearchProviderError):
            raise
        raise SearchProviderError(
            f"{provider_name} search failed: {error}"
        ) from error

    savepoint = f"search_run_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for query, result in results:
            metadata = dict(result)
            metadata["query"] = query
            profile = extract_result(metadata, project["company"])
            # Let the profile API default new records to pending without treating
            # that default as an instruction to downgrade reviewed duplicates.
            profile.pop("review_status")
            upsert_profile(conn, project_id, profile)
        conn.execute(
            """
            UPDATE search_runs
            SET status = 'completed', result_count = ?, error_message = NULL
            WHERE id = ?
            """,
            (len(results), run_id),
        )
    except Exception as error:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        _mark_failed(conn, run_id, error)
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return _run_row(conn, run_id)
