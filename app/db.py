"""SQLite connection and schema helpers."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DatabasePath = str | Path


def connect(path: DatabasePath) -> sqlite3.Connection:
    """Open a configured SQLite connection."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def transaction(path: DatabasePath) -> Iterator[sqlite3.Connection]:
    """Open a connection and commit or roll back its transaction."""
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(path: DatabasePath) -> None:
    """Create the application database schema when it does not exist."""
    with transaction(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                company TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS search_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL
                    REFERENCES projects(id) ON DELETE CASCADE,
                query TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                result_count INTEGER NOT NULL DEFAULT 0
                    CHECK (result_count >= 0),
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL
                    REFERENCES projects(id) ON DELETE CASCADE,
                dedupe_key TEXT NOT NULL,
                name TEXT,
                current_company TEXT,
                university TEXT,
                degree TEXT,
                location TEXT,
                role TEXT,
                years_experience REAL
                    CHECK (years_experience IS NULL OR years_experience >= 0),
                profile_url TEXT,
                normalized_url TEXT,
                source TEXT NOT NULL,
                source_query TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (review_status IN ('pending', 'verified', 'rejected')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (project_id, dedupe_key)
            );
            """
        )
