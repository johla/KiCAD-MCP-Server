"""Inspect the input without ever opening a writable SQLite connection."""

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


@contextmanager
def open_readonly(path):
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def inspect(connection):
    tables = []
    for name, sql in connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
    ):
        columns = [
            dict(zip(("cid", "name", "type", "notnull", "default", "pk", "hidden"), row))
            for row in connection.execute(f"PRAGMA table_xinfo({quote(name)})")
        ]
        tables.append({
            "name": name,
            "sql": sql,
            "columns": columns,
            "row_count": connection.execute(
                f"SELECT count(*) FROM {quote(name)}"
            ).fetchone()[0],
        })
    return tables
