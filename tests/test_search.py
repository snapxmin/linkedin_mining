import json
import sqlite3

import httpx
import pytest

from app.db import connect, init_db
from app.profiles import list_profiles, upsert_profile
from app.search import (
    DemoSearchProvider,
    SearchConfigurationError,
    SearchProviderError,
    SerperSearchProvider,
    build_queries,
    extract_result,
    run_search,
)


@pytest.fixture
def search_connection(tmp_path):
    database_path = tmp_path / "talent.db"
    init_db(database_path)
    connection = connect(database_path)
    project_id = connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("OKX research", "OKX"),
    ).lastrowid
    connection.commit()
    try:
        yield connection, project_id
    finally:
        connection.close()


def test_build_queries_quotes_company_and_adds_normalized_unique_roles():
    assert build_queries(
        '  ACME "Markets"  ',
        [" Staff   Engineer ", "staff engineer", "", "Data\\Scientist"],
    ) == [
        'site:linkedin.com/in "ACME Markets"',
        'site:linkedin.com/in "ACME Markets" "Staff Engineer"',
        'site:linkedin.com/in "ACME Markets" "Data Scientist"',
    ]


def test_build_queries_rejects_blank_company_and_non_text_roles():
    with pytest.raises(ValueError, match="company"):
        build_queries(" \t ", [])
    with pytest.raises(ValueError, match="role"):
        build_queries("OKX", [object()])


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Ada Lovelace - Research Lead - OKX | LinkedIn",
            {
                "name": "Ada Lovelace",
                "role": "Research Lead",
                "current_company": "OKX",
            },
        ),
        (
            "Grace Hopper – Principal Engineer at OKX | LinkedIn",
            {
                "name": "Grace Hopper",
                "role": "Principal Engineer",
                "current_company": "OKX",
            },
        ),
        (
            "Katherine Johnson | LinkedIn",
            {"name": "Katherine Johnson", "role": None, "current_company": None},
        ),
        (
            "Lin Chen - Engineer - Another Company | LinkedIn",
            {"name": "Lin Chen", "role": None, "current_company": None},
        ),
        (
            "Public profile directory",
            {"name": None, "role": None, "current_company": None},
        ),
    ],
)
def test_extract_result_only_returns_definite_title_metadata(title, expected):
    extracted = extract_result(
        {
            "title": title,
            "url": "https://www.linkedin.com/in/fictional-person",
            "query": 'site:linkedin.com/in "OKX"',
        },
        "OKX",
    )

    assert {field: extracted[field] for field in expected} == expected
    assert extracted["profile_url"] == (
        "https://www.linkedin.com/in/fictional-person"
    )
    assert extracted["source"] == "search"
    assert extracted["source_query"] == 'site:linkedin.com/in "OKX"'
    assert extracted["review_status"] == "pending"


def test_demo_provider_returns_repeatable_fictional_public_results():
    provider = DemoSearchProvider()
    query = 'site:linkedin.com/in "OKX" "Engineer"'

    first = provider.search(query)
    second = provider.search(query)

    assert first == second
    assert first
    assert all(result["query"] == query for result in first)
    assert all("fictional" in result["url"] for result in first)
    assert all(result["url"].startswith("https://www.linkedin.com/in/") for result in first)


def test_serper_maps_only_organic_payload_using_explicit_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Ada Lovelace - Research Lead - OKX | LinkedIn",
                        "link": "https://www.linkedin.com/in/fictional-ada",
                        "snippet": "Public search metadata.",
                        "position": 1,
                    }
                ],
                "peopleAlsoAsk": [{"link": "https://example.com/not-organic"}],
            },
        )

    provider = SerperSearchProvider(
        api_key="explicit-secret",
        timeout=3.5,
        transport=httpx.MockTransport(handler),
    )
    query = 'site:linkedin.com/in "OKX"'

    assert provider.search(query) == [
        {
            "title": "Ada Lovelace - Research Lead - OKX | LinkedIn",
            "url": "https://www.linkedin.com/in/fictional-ada",
            "snippet": "Public search metadata.",
            "query": query,
        }
    ]
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://google.serper.dev/search")
    assert requests[0].headers["X-API-KEY"] == "explicit-secret"
    assert json.loads(requests[0].content) == {"q": query}


def test_serper_reads_environment_key_and_applies_httpx_timeout(monkeypatch):
    observed = {}

    class RecordingTransport(httpx.BaseTransport):
        def handle_request(self, request):
            observed["timeout"] = request.extensions["timeout"]
            observed["key"] = request.headers["X-API-KEY"]
            return httpx.Response(200, json={"organic": []})

    monkeypatch.setenv("SERPER_API_KEY", "environment-secret")
    provider = SerperSearchProvider(timeout=2.25, transport=RecordingTransport())

    assert provider.search('site:linkedin.com/in "OKX"') == []
    assert observed["key"] == "environment-secret"
    assert set(observed["timeout"].values()) == {2.25}


def test_serper_reports_missing_configuration_and_http_failures(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(SearchConfigurationError, match="SERPER_API_KEY"):
        SerperSearchProvider()

    def handler(request):
        return httpx.Response(429, text="rate limited")

    provider = SerperSearchProvider(
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SearchProviderError, match=r"Serper.*429"):
        provider.search('site:linkedin.com/in "OKX"')


def test_serper_reports_invalid_payloads_as_provider_errors():
    def handler(request):
        return httpx.Response(200, text="not json")

    provider = SerperSearchProvider(
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SearchProviderError, match="JSON"):
        provider.search('site:linkedin.com/in "OKX"')


class StubProvider:
    name = "stub"

    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        role = "Engineer" if '"Engineer"' in query else "Researcher"
        return [
            {
                "title": f"Ada Lovelace - {role} - OKX | LinkedIn",
                "url": "https://www.linkedin.com/in/fictional-ada",
                "snippet": "",
                "query": query,
            }
        ]


def test_run_search_records_completed_run_and_upserts_pending_profiles(
    search_connection,
):
    connection, project_id = search_connection
    provider = StubProvider()

    run = run_search(connection, project_id, provider, ["Engineer", "engineer"])

    assert provider.queries == [
        'site:linkedin.com/in "OKX"',
        'site:linkedin.com/in "OKX" "Engineer"',
    ]
    assert run["status"] == "completed"
    assert run["provider"] == "stub"
    assert run["result_count"] == 2
    assert run["query"] == "\n".join(provider.queries)
    assert run["error_message"] is None
    profiles = list_profiles(connection, project_id)
    assert len(profiles) == 1
    assert profiles[0]["role"] == "Researcher"
    assert profiles[0]["source"] == "search"
    assert profiles[0]["source_query"] == provider.queries[0]
    assert profiles[0]["review_status"] == "pending"


def test_run_search_does_not_downgrade_a_verified_duplicate(search_connection):
    connection, project_id = search_connection
    original = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "profile_url": "https://www.linkedin.com/in/fictional-ada",
            "source": "manual",
            "review_status": "verified",
        },
    )

    run_search(connection, project_id, StubProvider(), [])

    profile = list_profiles(connection, project_id)[0]
    assert profile["id"] == original["id"]
    assert profile["review_status"] == "verified"


@pytest.mark.parametrize("review_status", ["verified", "rejected"])
def test_weak_search_duplicate_preserves_enrichment_provenance_and_review(
    search_connection,
    review_status,
):
    connection, project_id = search_connection
    original_data = {
        "name": "Manually Confirmed Name",
        "current_company": "Confirmed Company",
        "university": "Confirmed University",
        "degree": "Confirmed Degree",
        "location": "Confirmed Location",
        "role": "Confirmed Role",
        "years_experience": 12,
        "profile_url": "https://www.linkedin.com/in/fictional-ada",
        "source": "manual",
        "source_query": "manual verification notes",
        "review_status": review_status,
    }
    original = upsert_profile(connection, project_id, original_data)

    class WeakDuplicateProvider:
        name = "weak"

        def search(self, query):
            return [
                {
                    "title": "Public profile directory",
                    "url": "https://www.linkedin.com/in/fictional-ada",
                    "query": query,
                }
            ]

    run_search(connection, project_id, WeakDuplicateProvider(), [])

    profile = list_profiles(connection, project_id)[0]
    assert profile["id"] == original["id"]
    for field, expected in original_data.items():
        assert profile[field] == expected


def test_run_search_marks_failed_provider_run_without_profiles(search_connection):
    connection, project_id = search_connection

    class FailingProvider:
        name = "broken"

        def search(self, query):
            raise RuntimeError("service unavailable")

    with pytest.raises(SearchProviderError, match="service unavailable"):
        run_search(connection, project_id, FailingProvider(), ["Engineer"])

    run = dict(
        connection.execute(
            "SELECT * FROM search_runs WHERE project_id = ?", (project_id,)
        ).fetchone()
    )
    assert run["status"] == "failed"
    assert run["result_count"] == 0
    assert "service unavailable" in run["error_message"]
    assert list_profiles(connection, project_id) == []


@pytest.mark.parametrize(
    "malformed_result",
    [
        {},
        {
            "title": "Ada Lovelace | LinkedIn",
            "url": "javascript:alert(1)",
        },
    ],
)
def test_run_search_reports_unusable_metadata_and_writes_no_profiles(
    search_connection,
    malformed_result,
):
    connection, project_id = search_connection

    class MalformedProvider:
        name = "malformed"

        def search(self, query):
            return [
                {
                    "title": "Grace Hopper - Engineer - OKX | LinkedIn",
                    "url": "https://www.linkedin.com/in/fictional-grace",
                },
                malformed_result,
            ]

    with pytest.raises(SearchProviderError, match="invalid result metadata"):
        run_search(connection, project_id, MalformedProvider(), [])

    run = connection.execute(
        "SELECT status, result_count, error_message FROM search_runs"
    ).fetchone()
    assert tuple(run[:2]) == ("failed", 0)
    assert "invalid result metadata" in run["error_message"]
    assert list_profiles(connection, project_id) == []


def test_run_search_rolls_back_all_profile_writes_on_database_error(
    search_connection,
):
    connection, project_id = search_connection

    class TwoResultProvider:
        name = "two-results"

        def search(self, query):
            return [
                {
                    "title": "Ada Lovelace - Engineer - OKX | LinkedIn",
                    "url": "https://www.linkedin.com/in/fictional-ada",
                    "query": query,
                },
                {
                    "title": "Grace Hopper - Engineer - OKX | LinkedIn",
                    "url": "https://www.linkedin.com/in/fictional-grace",
                    "query": query,
                },
            ]

    connection.execute(
        """
        CREATE TRIGGER reject_grace_search
        BEFORE INSERT ON profiles
        WHEN NEW.name = 'Grace Hopper'
        BEGIN
            SELECT RAISE(ABORT, 'Grace rejected');
        END
        """
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="Grace rejected"):
        run_search(connection, project_id, TwoResultProvider(), [])

    run = connection.execute(
        "SELECT status, result_count, error_message FROM search_runs"
    ).fetchone()
    assert tuple(run[:2]) == ("failed", 0)
    assert "Grace rejected" in run["error_message"]
    assert list_profiles(connection, project_id) == []


def test_run_search_does_not_commit_an_existing_outer_transaction(search_connection):
    connection, project_id = search_connection
    connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("Uncommitted", "Example"),
    )

    run_search(connection, project_id, StubProvider(), [])
    connection.rollback()

    assert connection.execute(
        "SELECT COUNT(*) FROM projects WHERE name = 'Uncommitted'"
    ).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 0
    assert list_profiles(connection, project_id) == []
