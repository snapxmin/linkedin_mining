import sqlite3

import pytest

from app.db import connect, init_db, transaction


def test_init_db_creates_schema_and_profile_uniqueness_index(tmp_path):
    database_path = tmp_path / "talent.db"

    init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        unique_indexes = [
            row[1]
            for row in connection.execute("PRAGMA index_list(profiles)")
            if row[2]
        ]
        indexed_columns = {
            tuple(
                column[2]
                for column in connection.execute(f'PRAGMA index_info("{index}")')
            )
            for index in unique_indexes
        }

    assert {"projects", "search_runs", "profiles"} <= tables
    assert ("project_id", "dedupe_key") in indexed_columns


def test_connect_enables_foreign_keys_and_wal(tmp_path):
    with connect(tmp_path / "talent.db") as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode == "wal"


def test_transaction_commits_and_rolls_back(tmp_path):
    database_path = tmp_path / "talent.db"
    with transaction(database_path) as connection:
        connection.execute("CREATE TABLE values_for_test (value TEXT NOT NULL)")
        connection.execute("INSERT INTO values_for_test VALUES ('committed')")

    with pytest.raises(RuntimeError):
        with transaction(database_path) as connection:
            connection.execute("INSERT INTO values_for_test VALUES ('rolled back')")
            raise RuntimeError("stop transaction")

    with connect(database_path) as connection:
        values = [
            row[0] for row in connection.execute("SELECT value FROM values_for_test")
        ]

    assert values == ["committed"]


def test_schema_enforces_statuses_and_populates_timestamps(tmp_path):
    database_path = tmp_path / "talent.db"
    init_db(database_path)

    with transaction(database_path) as connection:
        project_id = connection.execute(
            "INSERT INTO projects (name, company) VALUES (?, ?)",
            ("OKX research", "OKX"),
        ).lastrowid

    with connect(database_path) as connection:
        project = connection.execute(
            "SELECT created_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    assert project["created_at"]

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(database_path) as connection:
            connection.execute(
                """
                INSERT INTO search_runs (project_id, query, provider, status)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, 'site:linkedin.com/in "OKX"', "demo", "invalid"),
            )

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(database_path) as connection:
            connection.execute(
                """
                INSERT INTO profiles (
                    project_id, dedupe_key, name, source, review_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, "profile-key", "Example Person", "csv", "invalid"),
            )
