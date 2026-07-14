import sqlite3

import pytest

from app.db import connect, init_db
from app.profiles import (
    list_profiles,
    normalize_url,
    profile_dedupe_key,
    update_profile,
    upsert_profile,
    validate_profile,
)


@pytest.fixture
def profile_connection(tmp_path):
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            " HTTPS://WWW.LinkedIn.com/in/Ada-Lovelace/?trk=public_profile ",
            "https://www.linkedin.com/in/Ada-Lovelace",
        ),
        (
            "http://example.com/public/profile/?source=search#summary",
            "http://example.com/public/profile",
        ),
        (
            "https://profiles.example.com:8443/public/person/",
            "https://profiles.example.com:8443/public/person",
        ),
    ],
)
def test_normalize_url_removes_query_fragment_and_trailing_slash(value, expected):
    assert normalize_url(value) == expected


@pytest.mark.parametrize("value", ["ftp://example.com/person", "mailto:a@example.com"])
def test_normalize_url_rejects_non_http_urls(value):
    with pytest.raises(ValueError, match="http"):
        normalize_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://example .com/in/ada",
        "https://example.com/in/ada lovelace",
        "https://-example.com/in/ada",
        "https://example..com/in/ada",
        "https://example_com/in/ada",
    ],
)
def test_normalize_url_rejects_whitespace_and_invalid_hosts(value):
    with pytest.raises(ValueError, match="valid"):
        normalize_url(value)


@pytest.mark.parametrize(
    "value",
    [
        r"https://example.com\@evil.com/x",
        "https://example.com@evil.com/x",
        r"https://example.com/public\@evil.com/x",
    ],
)
def test_normalize_url_rejects_authority_confusion(value):
    with pytest.raises(ValueError, match="valid"):
        normalize_url(value)


def test_normalize_url_handles_blank_values_deterministically():
    assert normalize_url(None) is None
    assert normalize_url(" \t ") is None


def test_url_backed_dedupe_keys_are_stable():
    first = profile_dedupe_key(
        {"profile_url": "https://linkedin.com/in/example/?trk=search"}
    )
    second = profile_dedupe_key(
        {"profile_url": " HTTPS://LINKEDIN.COM/in/example "}
    )

    assert first == second
    assert first.startswith("url:")


def test_profiles_without_urls_use_normalized_name_company_and_role():
    first = profile_dedupe_key(
        {
            "name": "  Ada   Lovelace ",
            "current_company": " ANALYTICAL   Engines ",
            "role": "Research LEAD",
        }
    )
    second = profile_dedupe_key(
        {
            "name": "ada lovelace",
            "current_company": "analytical engines",
            "role": " research lead ",
        }
    )
    changed_role = profile_dedupe_key(
        {
            "name": "ada lovelace",
            "current_company": "analytical engines",
            "role": "engineer",
        }
    )

    assert first == second
    assert first.startswith("identity:")
    assert changed_role != first


def test_validate_profile_normalizes_fields_and_rejects_invalid_values():
    validated = validate_profile(
        {
            "name": "  Ada   Lovelace ",
            "current_company": "",
            "years_experience": "4.5",
            "profile_url": "https://example.com/ada/?ref=result",
            "source": " csv ",
        }
    )

    assert validated["name"] == "Ada Lovelace"
    assert validated["current_company"] is None
    assert validated["years_experience"] == 4.5
    assert validated["profile_url"] == "https://example.com/ada"
    assert validated["review_status"] == "pending"
    assert validated["source"] == "csv"

    with pytest.raises(ValueError, match="source"):
        validate_profile({"name": "Ada"})
    with pytest.raises(ValueError, match="identity"):
        validate_profile({"source": "csv"})
    with pytest.raises(ValueError, match="review_status"):
        validate_profile(
            {"name": "Ada", "source": "csv", "review_status": "unreviewed"}
        )
    with pytest.raises(ValueError, match="years_experience"):
        validate_profile(
            {"name": "Ada", "source": "csv", "years_experience": -1}
        )


def test_validate_profile_treats_optional_whitespace_as_blank():
    validated = validate_profile(
        {
            "name": "Ada",
            "years_experience": "   ",
            "review_status": " ",
            "source": "manual",
        }
    )

    assert validated["years_experience"] is None
    assert validated["review_status"] == "pending"


def test_importing_same_profile_twice_updates_instead_of_duplicates(
    profile_connection,
):
    connection, project_id = profile_connection
    first = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "role": "Researcher",
            "profile_url": "https://linkedin.com/in/ada/?trk=search",
            "source": "search",
        },
    )
    second = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "role": "Research Lead",
            "profile_url": "https://LINKEDIN.com/in/ada",
            "source": "csv",
            "review_status": "verified",
        },
    )

    rows = list_profiles(connection, project_id)
    assert second["id"] == first["id"]
    assert len(rows) == 1
    assert rows[0]["role"] == "Research Lead"
    assert rows[0]["source"] == "csv"
    assert rows[0]["review_status"] == "verified"


def test_sparse_upsert_preserves_omitted_enrichment_and_review_status(
    profile_connection,
):
    connection, project_id = profile_connection
    original = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "current_company": "Analytical Engines",
            "university": "University of London",
            "role": "Researcher",
            "years_experience": 8,
            "profile_url": "https://linkedin.com/in/ada",
            "source": "csv",
            "review_status": "verified",
        },
    )

    repeated = upsert_profile(
        connection,
        project_id,
        {
            "role": "Research Lead",
            "profile_url": "https://linkedin.com/in/ada/?trk=search",
            "source": "search",
        },
    )

    assert repeated["id"] == original["id"]
    assert repeated["name"] == "Ada Lovelace"
    assert repeated["current_company"] == "Analytical Engines"
    assert repeated["university"] == "University of London"
    assert repeated["years_experience"] == 8
    assert repeated["role"] == "Research Lead"
    assert repeated["source"] == "search"
    assert repeated["review_status"] == "verified"


def test_dedupe_is_scoped_to_project(profile_connection):
    connection, first_project_id = profile_connection
    second_project_id = connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("Other research", "Other"),
    ).lastrowid
    data = {
        "name": "Ada Lovelace",
        "profile_url": "https://linkedin.com/in/ada",
        "source": "csv",
    }

    first = upsert_profile(connection, first_project_id, data)
    second = upsert_profile(connection, second_project_id, data)

    assert first["id"] != second["id"]


def test_list_profiles_filters_status_and_applies_pagination(profile_connection):
    connection, project_id = profile_connection
    for name, status in [
        ("Ada", "verified"),
        ("Grace", "pending"),
        ("Katherine", "verified"),
    ]:
        upsert_profile(
            connection,
            project_id,
            {"name": name, "source": "manual", "review_status": status},
        )

    rows = list_profiles(
        connection, project_id, review_status="verified", limit=1, offset=1
    )

    assert [row["name"] for row in rows] == ["Katherine"]


def test_update_profile_merges_then_validates_partial_changes(profile_connection):
    connection, project_id = profile_connection
    profile = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "role": "Researcher",
            "source": "csv",
        },
    )

    updated = update_profile(
        connection,
        profile["id"],
        {"role": "Research Lead", "review_status": "verified"},
    )

    assert updated["name"] == "Ada Lovelace"
    assert updated["role"] == "Research Lead"
    assert updated["review_status"] == "verified"

    with pytest.raises(ValueError, match="identity"):
        update_profile(
            connection,
            profile["id"],
            {"name": None, "current_company": None, "role": None},
        )
    with pytest.raises(ValueError, match="review_status"):
        update_profile(connection, profile["id"], {"review_status": "invalid"})

    unchanged = list_profiles(connection, project_id)[0]
    assert unchanged["name"] == "Ada Lovelace"
    assert unchanged["review_status"] == "verified"


def test_update_profile_rejects_unknown_fields_and_duplicate_identity(
    profile_connection,
):
    connection, project_id = profile_connection
    first = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada",
            "profile_url": "https://example.com/ada",
            "source": "manual",
        },
    )
    second = upsert_profile(
        connection,
        project_id,
        {
            "name": "Grace",
            "profile_url": "https://example.com/grace",
            "source": "manual",
        },
    )

    with pytest.raises(ValueError, match="unknown"):
        update_profile(connection, first["id"], {"project_id": 99})
    with pytest.raises(sqlite3.IntegrityError):
        update_profile(
            connection,
            second["id"],
            {"profile_url": "https://example.com/ada/"},
        )
