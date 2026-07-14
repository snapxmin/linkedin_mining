"""Scoped profile analytics and deterministic CSV export."""

import csv
import io
import sqlite3
from typing import Any


DIMENSIONS = ("university", "role", "location", "degree")
EXPORT_COLUMNS = (
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
)
SCOPES = frozenset({"verified", "all"})
FORMULA_PREFIXES = ("=", "+", "-", "@")


def _status_filter(scope: str) -> tuple[str, tuple[str, ...]]:
    if scope not in SCOPES:
        raise ValueError("scope must be verified or all")
    if scope == "verified":
        return "review_status = ?", ("verified",)
    return "review_status IN (?, ?)", ("pending", "verified")


def aggregate(
    conn: sqlite3.Connection, project_id: int, scope: str = "verified"
) -> dict[str, list[dict[str, Any]]]:
    """Return distributions for each supported dimension within a review scope."""
    status_sql, statuses = _status_filter(scope)
    statements: list[str] = []
    parameters: list[Any] = []
    for position, dimension in enumerate(DIMENSIONS):
        statements.append(
            f"""
            SELECT {position} AS dimension_order,
                   ? AS dimension,
                   COALESCE(NULLIF(TRIM({dimension}), ''), 'Unknown') AS label,
                   COUNT(*) AS count
            FROM profiles
            WHERE project_id = ? AND {status_sql}
            GROUP BY COALESCE(NULLIF(TRIM({dimension}), ''), 'Unknown')
            """
        )
        parameters.extend((dimension, project_id, *statuses))

    rows = conn.execute(
        f"""
        {" UNION ALL ".join(statements)}
        ORDER BY dimension_order,
                 count DESC,
                 label COLLATE NOCASE ASC,
                 label ASC
        """,
        parameters,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {
        dimension: [] for dimension in DIMENSIONS
    }
    for row in rows:
        result[row["dimension"]].append(
            {"label": row["label"], "count": row["count"]}
        )
    return result


def summary(
    conn: sqlite3.Connection, project_id: int, scope: str = "verified"
) -> dict[str, int | str]:
    """Return the sample size and included review-status counts."""
    status_sql, statuses = _status_filter(scope)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS sample_size,
               COALESCE(SUM(review_status = 'verified'), 0) AS verified,
               COALESCE(SUM(review_status = 'pending'), 0) AS pending
        FROM profiles
        WHERE project_id = ? AND {status_sql}
        """,
        (project_id, *statuses),
    ).fetchone()
    return {
        "scope": scope,
        "sample_size": row["sample_size"],
        "verified": row["verified"],
        "pending": row["pending"],
    }


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def export_csv(
    conn: sqlite3.Connection, project_id: int, scope: str = "verified"
) -> bytes:
    """Export normalized profiles in the requested scope as UTF-8 CSV bytes."""
    status_sql, statuses = _status_filter(scope)
    columns = ", ".join(EXPORT_COLUMNS)
    rows = conn.execute(
        f"""
        SELECT {columns}
        FROM profiles
        WHERE project_id = ? AND {status_sql}
        ORDER BY id ASC
        """,
        (project_id, *statuses),
    )

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            "" if row[column] is None else _csv_safe(row[column])
            for column in EXPORT_COLUMNS
        )
    return output.getvalue().encode("utf-8")
