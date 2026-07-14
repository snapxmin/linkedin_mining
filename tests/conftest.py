from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import transaction
from app.main import create_app
from app.profiles import upsert_profile


class FakeSearchProvider:
    name = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[dict[str, str]]:
        self.queries.append(query)
        role = "Engineer" if '"Engineer"' in query else "Researcher"
        return [
            {
                "title": f"Ada Lovelace - {role} - OKX | LinkedIn",
                "url": "https://www.linkedin.com/in/fictional-api-ada",
                "query": query,
            }
        ]


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable:
    created = 0

    def factory(search_provider=None):
        nonlocal created
        created += 1
        return create_app(
            database_path=tmp_path / f"api-{created}.db",
            search_provider=search_provider,
        )

    return factory


@pytest.fixture
def fake_provider() -> FakeSearchProvider:
    return FakeSearchProvider()


@pytest.fixture
def app(app_factory, fake_provider):
    return app_factory(fake_provider)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def project(client):
    response = client.post(
        "/api/projects",
        json={"name": "OKX research", "company": "OKX"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def add_profile(app, project):
    def add(**changes):
        data = {
            "name": "Ada Lovelace",
            "role": "Researcher",
            "source": "manual",
            "review_status": "pending",
        }
        data.update(changes)
        with transaction(app.state.database_path) as connection:
            return upsert_profile(connection, project["id"], data)

    return add
