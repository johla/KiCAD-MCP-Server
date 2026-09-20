"""Read-only, bounded search over CERN institutional component evidence."""

import argparse
import json
import sqlite3
from pathlib import Path

from .extract_components import extract
from .inspect_sqlite import inspect, open_readonly, sha256
from .normalize_identity import normalize_identity, text
from .normalize_status import normalize_status


def _field(fields, *names):
    """Return the first source value whose field name matches case-insensitively."""
    normalized = {name.casefold(): value for name, value in fields.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value is not None:
            return value
    return None


def _matches(row, query):
    """Match only scalar source values, never interpreting source data as a query."""
    return any(query in text(value).casefold() for value in row["fields"].values())


def _summary(row):
    """Expose useful source evidence without presenting it as verified part data."""
    fields = row["fields"]
    return {
        "source_id": row["source_id"],
        "source": row["source"],
        "component": normalize_identity(row),
        "cern_part_number": _field(fields, "Part Number"),
        "description": _field(fields, "Part Description", "Description"),
        "lifecycle": normalize_status(_field(fields, "Status")),
    }


def search_components(database, query, limit=25):
    """Search a CERN SQLite snapshot without modifying it or inferring correctness."""
    database = Path(database).resolve(strict=True)
    query = text(query).casefold()
    if not query:
        raise ValueError("query must contain non-whitespace text")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    before = sha256(database)
    with open_readonly(database) as connection:
        rows = extract(connection, inspect(connection), before)
        matches = [_summary(row) for row in rows if _matches(row, query)]
    after = sha256(database)
    if before != after:
        raise ValueError("SQLite changed during search; no results returned")

    return {
        "schema_version": "1.0",
        "query": query,
        "limit": limit,
        "total_matches": len(matches),
        "truncated": len(matches) > limit,
        "results": matches[:limit],
        "source": {
            "name": database.name,
            "sha256_before": before,
            "sha256_after": after,
            "read_mode": "sqlite_uri_mode_ro",
        },
        "evidence": {
            "authority": "institutional_database",
            "verification": "unverified",
            "datasheet_crosscheck": "not_implemented",
        },
        "limitations": [
            "Search finds source text only; it does not rank suitability or select parts.",
            "Results are institutional evidence, not manufacturer truth or availability data.",
        ],
    }


def main():
    """Provide a stable JSON CLI boundary for the MCP server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=Path("CERN.sqlite"))
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    try:
        result = search_components(args.sqlite, args.query, args.limit)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
