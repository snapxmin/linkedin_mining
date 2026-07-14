import csv
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.search import MAX_ROLE_LENGTH, MAX_SEARCH_ROLES


def test_health_reports_ready(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "company": "OKX"},
        {"name": " \t ", "company": "OKX"},
        {"name": "Research", "company": ""},
        {"name": "Research", "company": " \n "},
    ],
)
def test_project_creation_rejects_blank_name_and_company(client, payload):
    response = client.post("/api/projects", json=payload)

    assert response.status_code == 422


def test_project_creation_normalizes_and_returns_project(client):
    response = client.post(
        "/api/projects",
        json={"name": "  OKX   research ", "company": " OKX "},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "OKX research"
    assert response.json()["company"] == "OKX"
    assert response.json()["id"] > 0
    assert response.json()["created_at"]


def test_project_detail_returns_project_and_missing_is_404(client, project):
    response = client.get(f"/api/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json() == project
    assert client.get("/api/projects/999999").status_code == 404


def test_search_accepts_role_list_and_uses_injected_provider(
    client, project, fake_provider
):
    response = client.post(
        f"/api/projects/{project['id']}/search",
        json={"roles": ["Engineer", "engineer", ""]},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert response.json()["provider"] == "fake"
    assert response.json()["result_count"] == 2
    assert fake_provider.queries == [
        'site:linkedin.com/in "OKX"',
        'site:linkedin.com/in "OKX" "Engineer"',
    ]
    profiles = client.get(f"/api/projects/{project['id']}/profiles").json()
    assert profiles["total"] == 1
    assert profiles["items"][0]["source"] == "search"


def test_search_provider_errors_are_safe_and_failed_run_is_committed(app_factory):
    secret = "secret-token-that-must-not-leak"

    class FailingProvider:
        name = "failing"

        def search(self, query):
            raise RuntimeError(f"upstream refused {secret}")

    app = app_factory(FailingProvider())
    with TestClient(app, raise_server_exceptions=False) as client:
        project = client.post(
            "/api/projects", json={"name": "Research", "company": "OKX"}
        ).json()
        response = client.post(
            f"/api/projects/{project['id']}/search", json={"roles": []}
        )

        assert response.status_code == 502
        assert response.json() == {"detail": "Search provider unavailable"}
        assert secret not in response.text
        with connect(app.state.database_path) as connection:
            run = connection.execute(
                "SELECT status, error_message FROM search_runs WHERE project_id = ?",
                (project["id"],),
            ).fetchone()
        assert run["status"] == "failed"
        assert run["error_message"] is not None
        assert secret not in run["error_message"]


def test_search_missing_project_is_404(client):
    response = client.post("/api/projects/999999/search", json={"roles": []})

    assert response.status_code == 404


def test_csv_import_accepts_multipart_utf8_and_returns_profiles(client, project):
    content = (
        "Name,University,Role,URL,Review Status\n"
        "艾达,Oxford,Engineer,https://example.com/ada,verified\n"
    ).encode()

    response = client.post(
        f"/api/projects/{project['id']}/imports/csv",
        files={"file": ("profiles.csv", content, "text/csv")},
    )

    assert response.status_code == 201
    assert response.json()["imported_count"] == 1
    assert response.json()["items"][0]["name"] == "艾达"


def test_csv_import_maps_validation_errors_without_partial_writes(client, project):
    content = (
        "Name,URL,Years\n"
        "Ada,https://example.com/ada,2\n"
        "Grace,https://example.com/grace,not-a-number\n"
    )

    response = client.post(
        f"/api/projects/{project['id']}/imports/csv",
        files={"file": ("profiles.csv", content, "text/csv")},
    )

    assert response.status_code == 422
    assert "row 3" in response.json()["detail"]
    profiles = client.get(f"/api/projects/{project['id']}/profiles").json()
    assert profiles["total"] == 0


def test_csv_import_enforces_five_mibibyte_hard_limit(client, project):
    response = client.post(
        f"/api/projects/{project['id']}/imports/csv",
        files={"file": ("large.csv", b"x" * (5 * 1024 * 1024 + 1), "text/csv")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "CSV upload exceeds 5 MiB limit"}


def test_profiles_paginate_and_filter_review_status(client, project, add_profile):
    add_profile(name="Pending", review_status="pending")
    add_profile(name="Verified one", review_status="verified")
    add_profile(name="Verified two", review_status="verified")

    response = client.get(
        f"/api/projects/{project['id']}/profiles",
        params={"review_status": "verified", "page": 2, "page_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 1
    assert response.json()["total"] == 2
    assert [item["name"] for item in response.json()["items"]] == ["Verified two"]


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"page_size": 101}, 422),
        ({"page_size": 0}, 422),
        ({"page": 0}, 422),
        ({"review_status": "unknown"}, 422),
    ],
)
def test_profiles_validate_pagination_and_filter(client, project, params, status):
    response = client.get(
        f"/api/projects/{project['id']}/profiles", params=params
    )

    assert response.status_code == status


def test_profiles_missing_project_is_404(client):
    assert client.get("/api/projects/999999/profiles").status_code == 404


def test_patch_profile_updates_review_and_enrichment(client, add_profile):
    profile = add_profile()

    response = client.patch(
        f"/api/profiles/{profile['id']}",
        json={
            "university": " University of London ",
            "years_experience": 5.5,
            "review_status": "verified",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    assert response.json()["university"] == "University of London"
    assert response.json()["years_experience"] == 5.5
    assert response.json()["review_status"] == "verified"


def test_patch_profile_maps_missing_and_invalid_updates(client, add_profile):
    profile = add_profile()

    assert client.patch(
        "/api/profiles/999999", json={"review_status": "verified"}
    ).status_code == 404
    invalid = client.patch(
        f"/api/profiles/{profile['id']}", json={"review_status": "invalid"}
    )
    assert invalid.status_code == 422
    assert "review_status" in invalid.text
    assert client.patch(
        f"/api/profiles/{profile['id']}", json={"source": "forged"}
    ).status_code == 422
    assert client.patch(f"/api/profiles/{profile['id']}", json={}).status_code == 422


def test_analytics_supports_verified_and_all_scopes(client, project, add_profile):
    add_profile(name="Verified", university="Oxford", review_status="verified")
    add_profile(name="Pending", university="Cambridge", review_status="pending")
    add_profile(name="Rejected", university="MIT", review_status="rejected")

    verified = client.get(f"/api/projects/{project['id']}/analytics").json()
    all_profiles = client.get(
        f"/api/projects/{project['id']}/analytics", params={"scope": "all"}
    ).json()

    assert verified["summary"]["sample_size"] == 1
    assert verified["distributions"]["university"] == [
        {"label": "Oxford", "count": 1}
    ]
    assert all_profiles["summary"]["sample_size"] == 2
    assert {item["label"] for item in all_profiles["distributions"]["university"]} == {
        "Oxford",
        "Cambridge",
    }


def test_analytics_validates_scope_and_project(client, project):
    assert client.get(
        f"/api/projects/{project['id']}/analytics", params={"scope": "rejected"}
    ).status_code == 422
    assert client.get("/api/projects/999999/analytics").status_code == 404


def test_export_csv_has_utf8_download_headers_and_scope(
    client, project, add_profile
):
    add_profile(name="艾达", review_status="verified")
    add_profile(name="Pending", review_status="pending")

    response = client.get(
        f"/api/projects/{project['id']}/export.csv", params={"scope": "all"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="project-{project["id"]}-profiles-all.csv"'
    )
    rows = list(
        csv.DictReader(io.StringIO(response.content.decode("utf-8"), newline=""))
    )
    assert [row["name"] for row in rows] == ["艾达", "Pending"]


def test_export_validates_scope_and_project(client, project):
    assert client.get(
        f"/api/projects/{project['id']}/export.csv", params={"scope": "rejected"}
    ).status_code == 422
    assert client.get("/api/projects/999999/export.csv").status_code == 404


def test_concurrent_requests_do_not_fail_on_sqlite_threading(app_factory):
    app = app_factory()
    with TestClient(app) as test_client:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(test_client.get, "/health") for _ in range(40)
            ]
            responses = [future.result() for future in futures]
    assert all(response.status_code == 200 for response in responses)


def test_memory_database_supports_multiple_requests():
    app = create_app(database_path=":memory:")
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/projects", json={"name": "Memory", "company": "OKX"}
        )
        assert created.status_code == 201
        project_id = created.json()["id"]
        fetched = test_client.get(f"/api/projects/{project_id}")
        assert fetched.status_code == 200
        assert fetched.json()["company"] == "OKX"


def test_search_rejects_too_many_roles(client, project):
    response = client.post(
        f"/api/projects/{project['id']}/search",
        json={"roles": [f"role-{index}" for index in range(MAX_SEARCH_ROLES + 1)]},
    )

    assert response.status_code == 422
    assert str(MAX_SEARCH_ROLES) in response.text


def test_search_rejects_overlong_role(client, project):
    response = client.post(
        f"/api/projects/{project['id']}/search",
        json={"roles": ["x" * (MAX_ROLE_LENGTH + 1)]},
    )

    assert response.status_code == 422
    assert str(MAX_ROLE_LENGTH) in response.text
