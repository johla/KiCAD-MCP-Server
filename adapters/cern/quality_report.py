"""Auditable coverage metrics and explicit limits of the first-cut gate."""

from collections import Counter

from .anomaly_rules import RULES, anomaly_order


def quality_report(rows, components, reps, anomalies, tables, libraries, source):
    rules = Counter(item["rule"] for item in anomalies)
    statuses = {}
    for kind in ("symbol", "footprint"):
        statuses[kind] = dict(sorted(Counter(
            rep["geometry"]["status"] for rep in reps if rep["kind"] == kind
        ).items()))
    expected = sum(table["row_count"] for table in tables)
    unresolved = sum(rep["geometry"]["status"] != "resolved" for rep in reps)
    ambiguous = sum(bool(rep["geometry"].get("ambiguities")) for rep in reps)
    row_ids = {row["source_id"] for row in rows}
    linked_ids = {sid for component in components for sid in component["source_ids"]}
    source_columns = {
        table["name"]: {column["name"] for column in table["columns"] if column["hidden"] != 1}
        for table in tables
    }
    return {
        "schema_version": "1.0",
        "gate": "F0_CERN_INGESTION",
        "source": source,
        "counts": {
            "tables": len(tables),
            "expected_source_rows": expected,
            "raw_rows": len(rows),
            "normalized_components": len(components),
            "representations": len(reps),
            "anomalies": len(anomalies),
            "unresolved_representations": unresolved,
            "ambiguous_geometries": ambiguous,
        },
        "checks": {
            "all_rows_extracted": expected == len(rows) == len(row_ids),
            "all_sources_linked": row_ids == linked_ids,
            "all_source_columns_preserved": all(
                set(row["fields"]) == source_columns[row["source"]["table"]] for row in rows
            ),
            "source_hash_unchanged": source["sha256_before"] == source["sha256_after"],
            "all_lifecycle_values_normalized": sum(
                len(component["lifecycle"]) for component in components
            ) == len(rows),
            "all_reference_slots_recorded": len(reps) == 2 * len(rows),
            "geometry_complete": unresolved == 0 and ambiguous == 0,
        },
        "lifecycle_counts": dict(sorted(Counter(
            observation["normalized"] for component in components
            for observation in component["lifecycle"]
        ).items())),
        "reference_parse_counts": dict(sorted(Counter(
            rep["reference"]["status"] for rep in reps
        ).items())),
        "geometry_status_counts": statuses,
        "anomaly_counts": {rule: rules[rule] for rule in RULES},
        "top_100_anomalies": sorted(anomalies, key=anomaly_order)[:100],
        "library_evidence": {
            "provided": libraries.root is not None,
            "table_sha256": libraries.table_hashes,
            "table_errors": libraries.table_errors,
            "revision": "not_inferred; per-file SHA256 is authoritative provenance",
        },
        "authority": "institutional_evidence_not_manufacturer_truth",
        "verification": "unverified",
        "datasheet_crosscheck": "not_implemented",
        "limitations": [
            "No manufacturer datasheet retrieval or crosscheck; placeholders remain unresolved.",
            "No manufacturer aliases, MPN alternatives, or fuzzy identity merging.",
            "Geometry resolution establishes a library join, not electrical correctness.",
            "Ambiguous geometry is excluded from pin-count comparisons.",
            "Package and identity-family rules intentionally cover only explicit strong cases.",
            "Semantic generation checking currently covers only the RP2040 golden negative.",
            "No source fields, lifecycle labels, or suspicious descriptions are corrected.",
        ],
    }
