import csv
import io

import pytest

from app.analytics import aggregate, export_csv, summary
from app.db import connect, init_db
from app.profiles import upsert_profile


@pytest.fixture
def analytics_connection(tmp_path):
    database_path = tmp_path / "talent.db"
    init_db(database_path)
    connection = connect(database_path)
    first_project_id = connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("OKX research", "OKX"),
    ).lastrowid
    second_project_id = connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("Other research", "Other"),
    ).lastrowid

    profiles = [
        {
            "name": "Ada",
            "university": "Oxford",
            "role": "Engineer",
            "location": "London",
            "degree": "MSc",
            "source": "csv",
            "review_status": "verified",
        },
        {
            "name": "Grace",
            "university": "cambridge",
            "role": "Researcher",
            "location": "New York",
            "degree": "PhD",
            "source": "manual",
            "review_status": "verified",
        },
        {
            "name": "Katherine",
            "university": "Cambridge",
            "role": "Engineer",
            "location": "London",
            "degree": "",
            "source": "search",
            "review_status": "verified",
        },
        {
            "name": "Linus",
            "university": "Zurich",
            "role": "Founder",
            "location": "Helsinki",
            "degree": "MSc",
            "source": "search",
            "review_status": "pending",
        },
        {
            "name": "Rejected Person",
            "university": "Oxford",
            "role": "Engineer",
            "location": "London",
            "degree": "MSc",
            "source": "csv",
            "review_status": "rejected",
        },
    ]
    for profile in profiles:
        upsert_profile(connection, first_project_id, profile)

    upsert_profile(
        connection,
        second_project_id,
        {
            "name": "Other Project",
            "university": "Oxford",
            "role": "Engineer",
            "location": "London",
            "degree": "MSc",
            "source": "csv",
            "review_status": "verified",
        },
    )
    connection.commit()
    try:
        yield connection, first_project_id
    finally:
        connection.close()


def test_default_aggregate_is_verified_only_and_covers_every_dimension(
    analytics_connection,
):
    connection, project_id = analytics_connection

    result = aggregate(connection, project_id)

    assert result == {
        "university": [
            {"label": "Cambridge", "count": 1},
            {"label": "cambridge", "count": 1},
            {"label": "Oxford", "count": 1},
        ],
        "role": [
            {"label": "Engineer", "count": 2},
            {"label": "Researcher", "count": 1},
        ],
        "location": [
            {"label": "London", "count": 2},
            {"label": "New York", "count": 1},
        ],
        "degree": [
            {"label": "MSc", "count": 1},
            {"label": "PhD", "count": 1},
            {"label": "Unknown", "count": 1},
        ],
    }


def test_all_scope_includes_pending_but_never_rejected(analytics_connection):
    connection, project_id = analytics_connection

    result = aggregate(connection, project_id, scope="all")

    assert result["university"] == [
        {"label": "Cambridge", "count": 1},
        {"label": "cambridge", "count": 1},
        {"label": "Oxford", "count": 1},
        {"label": "Zurich", "count": 1},
    ]
    assert result["role"] == [
        {"label": "Engineer", "count": 2},
        {"label": "Founder", "count": 1},
        {"label": "Researcher", "count": 1},
    ]


def test_summary_reports_the_scoped_sample_and_status_counts(analytics_connection):
    connection, project_id = analytics_connection

    assert summary(connection, project_id) == {
        "scope": "verified",
        "sample_size": 3,
        "verified": 3,
        "pending": 0,
    }
    assert summary(connection, project_id, scope="all") == {
        "scope": "all",
        "sample_size": 4,
        "verified": 3,
        "pending": 1,
    }


def test_export_is_utf8_normalized_scoped_and_project_isolated(
    analytics_connection,
):
    connection, project_id = analytics_connection

    content = export_csv(connection, project_id, scope="all")

    assert isinstance(content, bytes)
    assert not content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"), newline="")))
    assert list(rows[0]) == [
        "name",
        "current_company",
        "university",
        "degree",
        "location",
        "role",
        "years_experience",
        "profile_url",
        "normalized_url",
        "source",
        "source_query",
        "review_status",
    ]
    assert [row["name"] for row in rows] == ["Ada", "Grace", "Katherine", "Linus"]
    assert [row["review_status"] for row in rows] == [
        "verified",
        "verified",
        "verified",
        "pending",
    ]
    assert rows[2]["degree"] == ""
    assert rows[3]["source"] == "search"

    verified_rows = list(
        csv.DictReader(
            io.StringIO(export_csv(connection, project_id).decode("utf-8"), newline="")
        )
    )
    assert [row["name"] for row in verified_rows] == ["Ada", "Grace", "Katherine"]


def test_export_mitigates_spreadsheet_formula_injection(tmp_path):
    database_path = tmp_path / "formula.db"
    init_db(database_path)
    connection = connect(database_path)
    project_id = connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("Formula research", "Example"),
    ).lastrowid
    try:
        upsert_profile(
            connection,
            project_id,
            {
                "name": "=HYPERLINK(\"https://evil.example\")",
                "current_company": "+cmd",
                "university": "-2+3",
                "degree": "@SUM(1,1)",
                "location": "Safe",
                "role": "Engineer",
                "source": "=IMPORTDATA(\"https://evil.example\")",
                "source_query": "@query",
                "review_status": "verified",
            },
        )

        row = next(
            csv.DictReader(
                io.StringIO(
                    export_csv(connection, project_id).decode("utf-8"), newline=""
                )
            )
        )

        for field in (
            "name",
            "current_company",
            "university",
            "degree",
            "source",
            "source_query",
        ):
            assert row[field].startswith("'")
        assert row["location"] == "Safe"
    finally:
        connection.close()


@pytest.mark.parametrize("function", [aggregate, summary, export_csv])
def test_invalid_scope_is_rejected(analytics_connection, function):
    connection, project_id = analytics_connection

    with pytest.raises(ValueError, match="scope"):
        function(connection, project_id, scope="rejected")
