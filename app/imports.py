"""CSV parsing and atomic profile import."""

import csv
import io
import sqlite3
import uuid
from typing import Any

from app.profiles import upsert_profile, validate_profile


HEADER_ALIASES = {
    "name": "name",
    "full name": "name",
    "current company": "current_company",
    "company": "current_company",
    "current_company": "current_company",
    "university": "university",
    "school": "university",
    "college": "university",
    "degree": "degree",
    "qualification": "degree",
    "location": "location",
    "city": "location",
    "role": "role",
    "current role": "role",
    "title": "role",
    "job title": "role",
    "years": "years_experience",
    "years experience": "years_experience",
    "years of experience": "years_experience",
    "experience": "years_experience",
    "years_experience": "years_experience",
    "url": "profile_url",
    "profile url": "profile_url",
    "linkedin url": "profile_url",
    "linkedin profile url": "profile_url",
    "profile_url": "profile_url",
    "review status": "review_status",
    "status": "review_status",
    "review_status": "review_status",
}


def _decode_content(content: bytes | str) -> str:
    if isinstance(content, bytes):
        if not content:
            raise ValueError("CSV content is empty")
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("CSV content must be valid UTF-8") from error
    if isinstance(content, str):
        if not content:
            raise ValueError("CSV content is empty")
        return content.removeprefix("\ufeff")
    raise ValueError("CSV content must be bytes or text")


def _normalize_header(header: str | None) -> str:
    if header is None:
        return ""
    return " ".join(
        header.strip().casefold().replace("-", " ").replace("_", " ").split()
    )


def _mapped_headers(fieldnames: list[str | None]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for header in fieldnames:
        normalized = _normalize_header(header)
        profile_field = HEADER_ALIASES.get(normalized)
        if profile_field is None:
            continue
        if profile_field in claimed:
            raise ValueError(
                "CSV header maps multiple columns to "
                f"{profile_field}: {claimed[profile_field]!r} and {header!r}"
            )
        if header is not None:
            mapped[header] = profile_field
            claimed[profile_field] = header
    if not mapped:
        raise ValueError("CSV header does not contain recognized profile columns")
    return mapped


def parse_csv(content: bytes | str) -> list[dict[str, Any]]:
    """Parse CSV bytes or text into sparse, normalized profile dictionaries."""
    text = _decode_content(content)
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        fieldnames = reader.fieldnames
        if fieldnames is None or not any(
            _normalize_header(header) for header in fieldnames
        ):
            raise ValueError("CSV header is empty")
        headers = _mapped_headers(fieldnames)
        profiles: list[dict[str, Any]] = []

        for row in reader:
            row_number = reader.line_num
            if None in row:
                raise ValueError(f"CSV row {row_number} has more values than headers")

            candidate: dict[str, Any] = {"source": "csv"}
            for header, profile_field in headers.items():
                value = row.get(header)
                if value is not None and value.strip():
                    candidate[profile_field] = value

            try:
                normalized = validate_profile(candidate)
            except ValueError as error:
                raise ValueError(f"CSV row {row_number}: {error}") from error
            profiles.append({field: normalized[field] for field in candidate})
    except csv.Error as error:
        raise ValueError(f"CSV is malformed: {error}") from error

    if not profiles:
        raise ValueError("CSV must contain at least one data row")
    return profiles


def import_csv(
    conn: sqlite3.Connection, project_id: int, content: bytes | str
) -> list[dict[str, Any]]:
    """Validate a complete CSV batch, then atomically upsert every profile."""
    profiles = parse_csv(content)
    savepoint = f"csv_import_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        imported = [
            upsert_profile(conn, project_id, profile) for profile in profiles
        ]
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return imported
