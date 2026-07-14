"""Profile normalization, validation, and SQLite persistence."""

import hashlib
import ipaddress
import json
import math
import re
import sqlite3
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PROFILE_FIELDS = (
    "name",
    "current_company",
    "university",
    "degree",
    "location",
    "role",
    "years_experience",
    "profile_url",
    "source",
    "source_query",
    "review_status",
)
TEXT_FIELDS = (
    "name",
    "current_company",
    "university",
    "degree",
    "location",
    "role",
    "source",
    "source_query",
)
REVIEW_STATUSES = frozenset({"pending", "verified", "rejected"})
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _normalize_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = " ".join(value.split())
    return normalized or None


def normalize_url(value: Any) -> str | None:
    """Return a canonical public HTTP(S) URL, or ``None`` for a blank value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("profile_url must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    if any(character.isspace() for character in normalized) or "\\" in normalized:
        raise ValueError("profile_url must be a valid http(s) URL")

    try:
        parts = urlsplit(normalized)
        host = parts.hostname
        parts.port
    except ValueError as error:
        raise ValueError("profile_url must be a valid http(s) URL") from error
    scheme = parts.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parts.netloc
        or host is None
        or not _valid_authority(parts.netloc, host)
        or not _valid_host(host)
    ):
        raise ValueError("profile_url must be a valid http(s) URL")

    path = parts.path.rstrip("/")
    return urlunsplit((scheme, parts.netloc.lower(), path, "", ""))


def _valid_authority(authority: str, parsed_host: str) -> bool:
    if any(character in authority for character in "\\@%/?#"):
        return False

    if authority.startswith("["):
        closing_bracket = authority.find("]")
        if closing_bracket < 0:
            return False
        raw_host = authority[1:closing_bracket]
        suffix = authority[closing_bracket + 1 :]
        if suffix and (
            not suffix.startswith(":")
            or not suffix[1:].isascii()
            or not suffix[1:].isdigit()
        ):
            return False
    else:
        if "[" in authority or "]" in authority or authority.count(":") > 1:
            return False
        raw_host, separator, port = authority.rpartition(":")
        if not separator:
            raw_host = authority
        elif not port.isascii() or not port.isdigit():
            return False

    return raw_host.casefold() == parsed_host.casefold()


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    if ":" in host or host.replace(".", "").isdigit():
        return False
    try:
        ascii_host = host.encode("idna").decode("ascii").removesuffix(".")
    except UnicodeError:
        return False
    return (
        0 < len(ascii_host) <= 253
        and all(HOST_LABEL.fullmatch(label) for label in ascii_host.split("."))
    )


def _identity_part(value: Any) -> str:
    normalized = _normalize_text(value, "identity field")
    return normalized.casefold() if normalized else ""


def profile_dedupe_key(data: Mapping[str, Any]) -> str:
    """Build a stable key from the URL or normalized identity fields."""
    normalized_url = normalize_url(data.get("profile_url"))
    if normalized_url:
        return f"url:{normalized_url}"

    identity = [
        _identity_part(data.get("name")),
        _identity_part(data.get("current_company")),
        _identity_part(data.get("role")),
    ]
    serialized = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"identity:{digest}"


def validate_profile(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate input and return all writable fields in normalized form."""
    if not isinstance(data, Mapping):
        raise ValueError("profile must be a mapping")

    unknown = set(data) - set(PROFILE_FIELDS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown profile fields: {names}")

    result = {field: _normalize_text(data.get(field), field) for field in TEXT_FIELDS}
    result["profile_url"] = normalize_url(data.get("profile_url"))

    years = data.get("years_experience")
    if years is None or (isinstance(years, str) and not years.strip()):
        result["years_experience"] = None
    else:
        if isinstance(years, bool):
            raise ValueError("years_experience must be a non-negative number")
        try:
            numeric_years = float(years)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "years_experience must be a non-negative number"
            ) from error
        if not math.isfinite(numeric_years) or numeric_years < 0:
            raise ValueError("years_experience must be a non-negative number")
        result["years_experience"] = numeric_years

    status = _normalize_text(data.get("review_status"), "review_status") or "pending"
    if status not in REVIEW_STATUSES:
        raise ValueError(
            "review_status must be pending, verified, or rejected"
        )
    result["review_status"] = status

    if result["source"] is None:
        raise ValueError("source is required")
    if not result["profile_url"] and not any(
        result[field] for field in ("name", "current_company", "role")
    ):
        raise ValueError(
            "profile identity requires a URL, name, current company, or role"
        )
    return result


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise LookupError("profile not found")
    return dict(row)


def upsert_profile(
    conn: sqlite3.Connection, project_id: int, data: Mapping[str, Any]
) -> dict[str, Any]:
    """Insert a profile or update supplied fields on an identity match."""
    profile = validate_profile(data)
    dedupe_key = profile_dedupe_key(profile)
    columns = ", ".join(PROFILE_FIELDS)
    placeholders = ", ".join("?" for _ in PROFILE_FIELDS)
    supplied_fields = set(data)
    updates = [
        f"{field} = excluded.{field}"
        for field in PROFILE_FIELDS
        if field in supplied_fields
    ]
    if "profile_url" in supplied_fields:
        updates.insert(0, "normalized_url = excluded.normalized_url")
    updates.append("updated_at = CURRENT_TIMESTAMP")
    values = [profile[field] for field in PROFILE_FIELDS]

    row = conn.execute(
        f"""
        INSERT INTO profiles (
            project_id, dedupe_key, normalized_url, {columns}
        ) VALUES (?, ?, ?, {placeholders})
        ON CONFLICT (project_id, dedupe_key) DO UPDATE SET
            {", ".join(updates)}
        RETURNING *
        """,
        (project_id, dedupe_key, profile["profile_url"], *values),
    ).fetchone()
    return _row_dict(row)


def list_profiles(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    review_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List a project's profiles in insertion order."""
    if review_status is not None and review_status not in REVIEW_STATUSES:
        raise ValueError(
            "review_status must be pending, verified, or rejected"
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")

    parameters: list[Any] = [project_id]
    where = "project_id = ?"
    if review_status is not None:
        where += " AND review_status = ?"
        parameters.append(review_status)
    parameters.extend((limit, offset))
    rows = conn.execute(
        f"""
        SELECT * FROM profiles
        WHERE {where}
        ORDER BY id
        LIMIT ? OFFSET ?
        """,
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def update_profile(
    conn: sqlite3.Connection, profile_id: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a validated partial update to one profile."""
    if not isinstance(changes, Mapping):
        raise ValueError("profile changes must be a mapping")
    unknown = set(changes) - set(PROFILE_FIELDS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown profile fields: {names}")

    current = conn.execute(
        "SELECT * FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    if current is None:
        raise LookupError("profile not found")

    merged = {field: current[field] for field in PROFILE_FIELDS}
    merged.update(changes)
    profile = validate_profile(merged)
    dedupe_key = profile_dedupe_key(profile)
    assignments = ", ".join(f"{field} = ?" for field in PROFILE_FIELDS)
    values = [profile[field] for field in PROFILE_FIELDS]
    row = conn.execute(
        f"""
        UPDATE profiles
        SET dedupe_key = ?,
            normalized_url = ?,
            {assignments},
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        RETURNING *
        """,
        (dedupe_key, profile["profile_url"], *values, profile_id),
    ).fetchone()
    return _row_dict(row)
