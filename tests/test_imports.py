import sqlite3

import pytest

from app.db import connect, init_db
from app.imports import import_csv, parse_csv
from app.profiles import list_profiles, upsert_profile


@pytest.fixture
def import_connection(tmp_path):
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


def test_parse_csv_accepts_display_header_aliases_and_numeric_years():
    profiles = parse_csv(
        "Name,Current Company,University,Degree,Location,Role,Years,URL,"
        "Review Status\n"
        " Ada  Lovelace , Analytical Engines ,London,Mathematics,London,"
        "Research Lead,4.5,https://example.com/ada/?ref=csv,verified\n"
    )

    assert profiles == [
        {
            "name": "Ada Lovelace",
            "current_company": "Analytical Engines",
            "university": "London",
            "degree": "Mathematics",
            "location": "London",
            "role": "Research Lead",
            "years_experience": 4.5,
            "profile_url": "https://example.com/ada",
            "review_status": "verified",
            "source": "csv",
        }
    ]


@pytest.mark.parametrize(
    "header",
    [
        (
            "Full Name,Company,School,Qualification,City,Job Title,"
            "Years Experience,Profile URL,Status"
        ),
        (
            "name,current_company,university,degree,location,role,"
            "years_experience,profile_url,review_status"
        ),
    ],
)
def test_parse_csv_accepts_common_and_canonical_header_aliases(header):
    row = (
        "Ada Lovelace,Analytical Engines,London,Mathematics,London,"
        "Research Lead,4,https://example.com/ada,verified\n"
    )

    profile = parse_csv(f"{header}\n{row}")[0]

    assert profile["current_company"] == "Analytical Engines"
    assert profile["years_experience"] == 4.0
    assert profile["profile_url"] == "https://example.com/ada"
    assert profile["review_status"] == "verified"


def test_parse_csv_accepts_utf8_bom_in_bytes_and_text():
    body = "Name,URL\nAda,https://example.com/ada\n"

    assert parse_csv(b"\xef\xbb\xbf" + body.encode("utf-8"))[0]["name"] == "Ada"
    assert parse_csv("\ufeff" + body)[0]["name"] == "Ada"


def test_parse_csv_reports_physical_row_number_for_invalid_rows():
    content = (
        "Name,URL,Years\n"
        "Ada,https://example.com/ada,2\n"
        "Grace,https://example.com/grace,not-a-number\n"
    )

    with pytest.raises(ValueError, match=r"row 3.*years_experience"):
        parse_csv(content)


def test_parse_csv_rejects_rows_without_identity_fields():
    with pytest.raises(ValueError, match=r"row 2.*identity"):
        parse_csv("University,Degree\nLondon,Mathematics\n")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),
        (b"", "empty"),
        ("\n", "header"),
        ("Name,URL\n", "data row"),
        (b"\xffName,URL\nAda,https://example.com/ada\n", "UTF-8"),
    ],
)
def test_parse_csv_rejects_empty_or_malformed_content(content, message):
    with pytest.raises(ValueError, match=message):
        parse_csv(content)


def test_import_csv_validates_entire_batch_before_writing(import_connection):
    connection, project_id = import_connection
    content = (
        "Name,URL,Years\n"
        "Ada,https://example.com/ada,2\n"
        "Grace,https://example.com/grace,invalid\n"
    )

    with pytest.raises(ValueError, match=r"row 3"):
        import_csv(connection, project_id, content)

    assert list_profiles(connection, project_id) == []


def test_import_csv_rolls_back_all_rows_on_database_error(import_connection):
    connection, project_id = import_connection
    content = (
        "Name,URL\n"
        "Ada,https://example.com/ada\n"
        "Grace,https://example.com/grace\n"
    )
    connection.execute(
        """
        CREATE TRIGGER reject_grace
        BEFORE INSERT ON profiles
        WHEN NEW.name = 'Grace'
        BEGIN
            SELECT RAISE(ABORT, 'Grace rejected');
        END
        """
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="Grace rejected"):
        import_csv(connection, project_id, content)

    assert list_profiles(connection, project_id) == []


def test_duplicate_import_updates_without_erasing_omitted_fields(
    import_connection,
):
    connection, project_id = import_connection
    original = upsert_profile(
        connection,
        project_id,
        {
            "name": "Ada Lovelace",
            "current_company": "Analytical Engines",
            "university": "University of London",
            "degree": "Mathematics",
            "location": "London",
            "role": "Researcher",
            "years_experience": 8,
            "profile_url": "https://example.com/ada",
            "source": "manual",
            "source_query": "original query",
            "review_status": "verified",
        },
    )
    connection.commit()

    imported = import_csv(
        connection,
        project_id,
        "URL,Role\nhttps://example.com/ada/?ref=csv,Research Lead\n",
    )[0]

    assert imported["id"] == original["id"]
    assert imported["name"] == "Ada Lovelace"
    assert imported["current_company"] == "Analytical Engines"
    assert imported["university"] == "University of London"
    assert imported["degree"] == "Mathematics"
    assert imported["location"] == "London"
    assert imported["years_experience"] == 8
    assert imported["source_query"] == "original query"
    assert imported["review_status"] == "verified"
    assert imported["role"] == "Research Lead"
    assert imported["source"] == "csv"


def test_import_csv_does_not_commit_an_existing_outer_transaction(
    import_connection,
):
    connection, project_id = import_connection
    connection.execute(
        "INSERT INTO projects (name, company) VALUES (?, ?)",
        ("Uncommitted", "Example"),
    )

    import_csv(
        connection,
        project_id,
        "Name,URL\nAda,https://example.com/ada\n",
    )
    connection.rollback()

    assert connection.execute(
        "SELECT COUNT(*) FROM projects WHERE name = 'Uncommitted'"
    ).fetchone()[0] == 0
    assert list_profiles(connection, project_id) == []
