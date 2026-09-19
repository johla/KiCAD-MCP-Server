"""Conservative manufacturer/MPN grouping; representations are not identities."""

from .extract_components import stable_id


def text(value):
    return " ".join(str(value).split()) if value is not None else ""


def normalize_identity(row):
    fields = row["fields"]
    manufacturer = text(fields.get("Manufacturer")).casefold()
    mpn = text(fields.get("Manufacturer Part Number"))
    complete = manufacturer.casefold() not in ("", "none", "n/a", "-") and (
        mpn.casefold() not in ("", "none", "n/a", "-")
    )
    # Incomplete identities must not merge unrelated source records.
    key = [manufacturer, mpn] if complete else ["source", row["source_id"]]
    return {
        "component_id": stable_id("component", key),
        "manufacturer": manufacturer or None,
        "manufacturer_part_number": mpn or None,
        "identity_complete": complete,
        "identity_policy": "manufacturer-casefold_mpn-case-sensitive_whitespace-collapsed",
    }
