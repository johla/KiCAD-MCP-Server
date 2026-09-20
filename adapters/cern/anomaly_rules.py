"""Conservative evidence conflicts, never automatic corrections or truth claims."""

import re

from .extract_components import stable_id
from .normalize_status import normalize_status

RULES = (
    "pin_count_mismatch", "package_mismatch", "mounting_mismatch",
    "identity_collision", "datasheet_placeholder_unresolved",
    "lifecycle_normalization_needed", "semantic_generation_mismatch",
)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def anomaly(rule, source_ids, component_id, evidence, severity="medium"):
    return {
        "schema_version": "1.0",
        "anomaly_id": stable_id("anomaly", [rule, sorted(source_ids), evidence]),
        "rule": rule,
        "severity": severity,
        "source_ids": sorted(source_ids),
        "component_id": component_id,
        "evidence": evidence,
        "verification": "unverified",
        "action": "review_against_manufacturer_evidence",
    }


def package_family(value):
    # Only explicit package names; numerical sizes and arbitrary IPC names are not guesses.
    matches = re.findall(r"(?<![A-Z])(QFN|DFN|QFP|BGA|SOIC|TSSOP|SSOP|DIP|DIL)(?=\d|\b)",
                         str(value or "").upper())
    families = {"DIP" if family == "DIL" else family for family in matches}
    return next(iter(families)) if len(families) == 1 else None


def identity_family(row):
    table = row["source"]["table"].casefold()
    for prefix, family in (
        ("capacitors", "capacitor"), ("resistor", "resistor"),
        ("potentiometers", "resistor"), ("inductors", "inductor"),
        ("transistors", "transistor"), ("diodes", "diode"),
        ("fasteners", "mechanical"), ("heat-sinks", "mechanical"),
    ):
        if table.startswith(prefix):
            return family
    return None


def row_anomalies(row, component_id, reps):
    fields = row["fields"]
    source_ids = [row["source_id"]]

    def emit(rule, evidence, severity="medium"):
        return anomaly(rule, source_ids, component_id, evidence, severity)

    counts = {}
    declared = str(fields.get("Pin Count", "")).strip()
    if declared.isdigit() and int(declared) > 0:
        counts["source_pin_count"] = int(declared)
    for rep in reps:
        geometry = rep["geometry"]
        if geometry.get("status") == "resolved" and geometry.get("count_comparable"):
            counts[rep["kind"] + "_physical_count"] = geometry["physical_count"]
    if len(set(counts.values())) > 1:
        yield emit("pin_count_mismatch", counts)

    families = {}
    for key in ("Case", "PackageDescription"):
        family = package_family(fields.get(key))
        if family:
            families[key] = family
    footprint = next(rep for rep in reps if rep["kind"] == "footprint")
    family = package_family(footprint["reference"]["name"])
    if family:
        families["footprint_reference"] = family
    if len(set(families.values())) > 1:
        yield emit("package_mismatch", families)

    mounting = footprint["geometry"].get("mounting")
    source_smd = str(fields.get("SMD", "")).strip().casefold()
    if (source_smd, mounting) in (("yes", "through_hole"), ("no", "smd")):
        yield emit("mounting_mismatch", {
            "source_smd": fields["SMD"], "footprint_mounting": mounting,
        })

    datasheet = fields.get("Datasheet")
    if isinstance(datasheet, str) and re.search(r"\$\{[^}]+\}", datasheet):
        yield emit("datasheet_placeholder_unresolved", {
            "source_value": datasheet,
            "reason": "environment placeholders deliberately not expanded",
        }, "low")

    status = normalize_status(fields.get("Status"))
    if status["normalization_needed"]:
        yield emit("lifecycle_normalization_needed", status, "low")

    identity_text = " ".join(str(fields.get(key) or "") for key in
                             ("Part Number", "Manufacturer Part Number", "Device"))
    # Only the bare silicon identity, not modules which happen to mention RP2040.
    rp2040 = any(re.fullmatch(r"RP2040(?:\s*/\s*SC0914)?", str(fields.get(key) or ""), re.I)
                 for key in ("Part Number", "Manufacturer Part Number", "Device"))
    description = str(fields.get("Part Description") or "")
    if rp2040:
        claims = []
        for label, pattern in (
            ("Cortex-M33", r"\b(?:Cortex[- ]?)?M33\b"),
            ("Hazard3", r"\bHazard\s*3\b"),
            ("150MHz", r"\b150\s*MHz\b"),
            ("520KB SRAM", r"\b520\s*K[Bb]\b"),
            ("internal flash", r"\b(?:internal|on[- ]chip)\s+flash\b"),
        ):
            matches = re.finditer(pattern, description, re.I)
            if any(not re.search(r"\b(?:no|not|without|lacks|not have|not include)\s*$",
                                 description[:match.start()], re.I) for match in matches):
                claims.append(label)
        if claims:
            yield emit("semantic_generation_mismatch", {
                "identity": identity_text,
                "source_description": description,
                "conflicting_claims": claims,
                "expected_family": "RP2040: dual Cortex-M0+, 133MHz, 264KB SRAM; external flash",
                "rule_basis": "curated RP2040 golden negative; not a datasheet crosscheck",
                "reference": "https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf",
            }, "high")


def collision_anomalies(components, rows_by_id):
    for component in components:
        families = {}
        for source_id in component["source_ids"]:
            family = identity_family(rows_by_id[source_id])
            if family:
                families.setdefault(family, []).append(source_id)
        if len(families) > 1:
            yield anomaly(
                "identity_collision", component["source_ids"], component["component_id"],
                {"incompatible_source_families": families}, "high",
            )


def anomaly_order(item):
    return SEVERITY_ORDER[item["severity"]], item["rule"], item["anomaly_id"]
