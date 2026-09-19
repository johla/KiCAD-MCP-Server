"""CLI: python -m adapters.cern --sqlite CERN.sqlite [--libraries CHECKOUT]."""

import argparse
import json
import sqlite3
from pathlib import Path

from .anomaly_rules import anomaly_order, collision_anomalies, row_anomalies
from .extract_components import extract
from .inspect_sqlite import inspect, open_readonly, sha256
from .join_kicad_footprints import FootprintJoiner
from .join_kicad_symbols import SymbolJoiner
from .kicad import Libraries
from .normalize_identity import normalize_identity, text
from .normalize_representations import representations
from .normalize_status import normalize_status
from .quality_report import quality_report

OUTPUTS = (
    "components.raw.jsonl", "components.normalized.jsonl", "representations.jsonl",
    "anomalies.jsonl", "quality_report.json", "sqlite_inventory.json",
)


def dump(path, value, lines=False):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        if lines:
            for item in value:
                stream.write(json.dumps(item, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n")
        else:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")


def ingest(database, output, library_root=None):
    database = Path(database).resolve(strict=True)
    wal = Path(str(database) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("active SQLite WAL: provide a quiescent standalone snapshot")
    output = Path(output).resolve()
    if output == database or database in output.parents:
        raise ValueError("output must be separate from the SQLite source")
    for name in OUTPUTS:
        destination = output / name
        if destination.is_symlink() or destination.resolve() == database:
            raise ValueError("unsafe output file: " + name)
        if destination.exists() and destination.samefile(database):
            raise ValueError("output aliases the SQLite source")
    if library_root:
        root = Path(library_root).resolve(strict=True)
        if output == root or root in output.parents:
            raise ValueError("output must not be inside the read-only library evidence")
    before = sha256(database)
    with open_readonly(database) as connection:
        tables = inspect(connection)
        rows = list(extract(connection, tables, before))
    after = sha256(database)
    if before != after:
        raise ValueError("SQLite changed during extraction; no outputs written")
    libraries = Libraries(library_root)
    joiners = {"symbol": SymbolJoiner(libraries), "footprint": FootprintJoiner(libraries)}
    components = {}
    reps = []
    anomalies = []
    for row in rows:
        identity = normalize_identity(row)
        component_id = identity["component_id"]
        component = components.setdefault(component_id, {
            "schema_version": "1.0", **identity,
            "source_ids": [], "cern_part_numbers": [], "field_evidence": [],
            "representation_ids": [], "lifecycle": [],
            "evidence": {
                "authority": "institutional_database",
                "verification": "unverified",
                "confidence": "unverified",
                "datasheet_crosscheck": "not_implemented",
            },
        })
        component["source_ids"].append(row["source_id"])
        part_number = text(row["fields"].get("Part Number"))
        if part_number and part_number not in component["cern_part_numbers"]:
            component["cern_part_numbers"].append(part_number)
        normalized_fields = {
            "Manufacturer": identity["manufacturer"],
            "Manufacturer Part Number": identity["manufacturer_part_number"],
            "Part Number": part_number or None,
            "Status": normalize_status(row["fields"].get("Status"))["normalized"],
        }
        for field, source_value in sorted(row["fields"].items()):
            component["field_evidence"].append({
                "source_id": row["source_id"],
                "field": field,
                "source_value": source_value,
                "normalized_value": normalized_fields.get(field, source_value),
                "claim": "source_assertion",
                "authority": "institutional_database",
                "confidence": "unverified",
                "verification_state": "unverified",
            })
        component["lifecycle"].append({
            "source_id": row["source_id"],
            **normalize_status(row["fields"].get("Status")),
        })
        row_reps = list(representations(row, component_id))
        for rep in row_reps:
            rep["geometry"] = joiners[rep["kind"]].join(rep["reference"])
            component["representation_ids"].append(rep["representation_id"])
        anomalies.extend(row_anomalies(row, component_id, row_reps))
        reps.extend(row_reps)
    components = sorted(components.values(), key=lambda item: item["component_id"])
    for component in components:
        component["source_ids"].sort()
        component["cern_part_numbers"].sort()
        component["field_evidence"].sort(key=lambda item: (item["source_id"], item["field"]))
        component["representation_ids"].sort()
        component["lifecycle"].sort(key=lambda item: item["source_id"])
    anomalies.extend(collision_anomalies(components, {row["source_id"]: row for row in rows}))
    anomalies.sort(key=anomaly_order)
    reps.sort(key=lambda item: item["representation_id"])
    report = quality_report(rows, components, reps, anomalies, tables, libraries, {
        "name": database.name, "sha256_before": before, "sha256_after": after,
        "read_mode": "sqlite_uri_mode_ro",
    })
    if not all(value for key, value in report["checks"].items() if key != "geometry_complete"):
        raise ValueError("extraction integrity gate failed")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("components.raw.jsonl", rows), ("components.normalized.jsonl", components),
        ("representations.jsonl", reps), ("anomalies.jsonl", anomalies),
    ):
        dump(output / name, data, lines=True)
    dump(output / "sqlite_inventory.json", {"schema_version": "1.0", "tables": tables})
    dump(output / "quality_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=Path("CERN.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("generated/cern"))
    parser.add_argument("--libraries", type=Path, help="Existing CERN KiCad checkout/archive root")
    parser.add_argument("--require-complete-geometry", action="store_true")
    args = parser.parse_args()
    try:
        report = ingest(args.sqlite, args.output, args.libraries)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps({"counts": report["counts"], "checks": report["checks"]}, sort_keys=True))
    return 2 if args.require_complete_geometry and not report["checks"]["geometry_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
