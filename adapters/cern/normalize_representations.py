"""Parse references without expanding environment variables or traversing paths."""

from .extract_components import stable_id


def parse_reference(value):
    result = {"source_value": value, "library": None, "name": None, "status": "missing"}
    if value is None or not str(value).strip():
        return result
    if not isinstance(value, str) or ":" not in value:
        result["status"] = "malformed"
        return result
    library, name = value.split(":", 1)
    if not library or not name or "\x00" in value:
        result["status"] = "malformed"
        return result
    result.update(library=library, name=name, status="parsed")
    return result


def representations(row, component_id):
    for kind, field in (("symbol", "LibSymbol"), ("footprint", "LibFootprint")):
        yield {
            "schema_version": "1.0",
            "representation_id": stable_id("representation", [row["source_id"], kind]),
            "source_id": row["source_id"],
            "component_id": component_id,
            "kind": kind,
            "reference": parse_reference(row["fields"].get(field)),
            "evidence": {
                "authority": "institutional_library",
                "verification": "unverified",
                "confidence": "unverified",
                "datasheet_crosscheck": "not_implemented",
            },
        }
