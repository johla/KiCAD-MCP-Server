"""Lossless SQLite rows with content-addressed source provenance."""

import base64
import hashlib
import json
import math
import sqlite3

from .inspect_sqlite import quote


def stable_id(prefix, value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_value(value):
    if isinstance(value, bytes):
        return {"$sqlite_blob_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$sqlite_float": repr(value)}
    return value


def extract(connection, tables, database_sha256):
    for table in tables:
        columns = [column["name"] for column in table["columns"] if column["hidden"] != 1]
        names = {name.casefold() for name in columns}
        rowid = next((name for name in ("rowid", "_rowid_", "oid")
                      if name not in names), None)
        if rowid:
            try:
                connection.execute(f"SELECT {rowid} FROM {quote(table['name'])} LIMIT 0")
            except sqlite3.OperationalError:
                rowid = None
        primary = [c["name"] for c in sorted(table["columns"], key=lambda c: c["pk"])
                   if c["pk"]]
        order = [rowid] if rowid else primary or columns
        selection = ([quote(rowid)] if rowid else []) + [quote(c) for c in columns]
        query = (f"SELECT {', '.join(selection)} FROM {quote(table['name'])} "
                 f"ORDER BY {', '.join(quote(c) for c in order)}")
        for ordinal, row in enumerate(connection.execute(query), 1):
            fields = dict(zip(columns, map(json_value, row[1:] if rowid else row)))
            key = {"rowid": row[0]} if rowid else {
                "primary_key": {name: fields[name] for name in primary},
                "ordinal": ordinal,
            }
            source = {
                "database_sha256": database_sha256,
                "table": table["name"],
                "key": key,
                "ordinal": ordinal,
                "part_number": fields.get("Part Number"),
            }
            yield {
                "schema_version": "1.0",
                "source_id": stable_id("cern_row", [database_sha256, table["name"], key]),
                "source": source,
                "fields": fields,
            }
