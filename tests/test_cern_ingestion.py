"""F0 contracts and conservative evidence handling, without KiCad or network access."""

import copy
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from adapters.cern.__main__ import OUTPUTS, ingest
from adapters.cern.anomaly_rules import (
    anomaly_order,
    collision_anomalies,
    package_family,
    row_anomalies,
)
from adapters.cern.catalog import search_components
from adapters.cern.extract_components import extract, json_value
from adapters.cern.inspect_sqlite import inspect, open_readonly, quote, sha256
from adapters.cern.join_kicad_footprints import FootprintJoiner
from adapters.cern.join_kicad_symbols import SymbolJoiner
from adapters.cern.kicad import Libraries, parse
from adapters.cern.normalize_identity import normalize_identity
from adapters.cern.normalize_representations import parse_reference, representations
from adapters.cern.normalize_status import normalize_status

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/cern.schema.json").read_text())
CONTRACTS = {
    "components.raw.jsonl": ("raw", "cern.raw-component.schema.json"),
    "components.normalized.jsonl": ("component", "cern.component.schema.json"),
    "representations.jsonl": ("representation", "cern.representation.schema.json"),
    "anomalies.jsonl": ("anomaly", "cern.anomaly.schema.json"),
    "quality_report.json": ("report", "cern.quality-report.schema.json"),
    "sqlite_inventory.json": ("inventory", "cern.sqlite-inventory.schema.json"),
}


def validate(value, schema):
    """Exercise every assertion keyword used by our contracts, not a general validator."""
    supported = {
        "$schema", "$ref", "title", "description", "type", "required", "properties",
        "additionalProperties", "const", "enum", "pattern", "minimum", "minItems",
        "maxItems", "minLength", "items", "uniqueItems", "allOf", "if", "then",
    }
    assert set(schema) <= supported, "Extend this test validator when adding contract keywords"
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/$defs/")
        validate(value, SCHEMA["$defs"][ref.rsplit("/", 1)[-1]])
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        actual = {
            dict: "object", list: "array", str: "string", int: "integer",
            float: "number", bool: "boolean", type(None): "null",
        }[type(value)]
        assert actual in types
    if "const" in schema:
        assert value == schema["const"]
    if "enum" in schema:
        assert value in schema["enum"]
    if "pattern" in schema:
        assert re.search(schema["pattern"], value)
    if "minimum" in schema:
        assert value >= schema["minimum"]
    if "minLength" in schema:
        assert len(value) >= schema["minLength"]
    if isinstance(value, dict):
        assert set(schema.get("required", [])) <= set(value)
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key])
            elif additional is False:
                raise AssertionError("Unexpected property: " + key)
            elif isinstance(additional, dict):
                validate(item, additional)
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0)
        assert len(value) <= schema.get("maxItems", len(value))
        if schema.get("uniqueItems"):
            assert len({json.dumps(item, sort_keys=True) for item in value}) == len(value)
        for item in value:
            validate(item, schema.get("items", {}))
    for part in schema.get("allOf", []):
        validate(value, part)
    if "if" in schema:
        try:
            validate(value, schema["if"])
        except AssertionError:
            pass
        else:
            validate(value, schema.get("then", {}))


@pytest.fixture
def work():
    directory = ROOT / "generated/cern/test-work" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)


def make_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE "Logic" (
            "Part Number" TEXT, "Manufacturer" TEXT, "Manufacturer Part Number" TEXT,
            "Status" TEXT, "Pin Count" TEXT, "Case" TEXT, "PackageDescription" TEXT,
            "SMD" TEXT, "Part Description" TEXT, "LibSymbol" TEXT,
            "LibFootprint" TEXT, "Datasheet" TEXT, "Extra Field" BLOB)''')
        connection.executemany('INSERT INTO "Logic" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            ("RP2040", "RASPBERRY", "RP2040 / SC0914", None, "3", "QFN56",
             "QFN", "Yes", "Dual Cortex-M33 or Hazard3 @ 150MHz, 520 KB, 2 MB Internal Flash",
             "Logic:Derived", "Packages:QFN56", "${CERN_DATASHEET_DIR}\\RP2040.pdf", b"\x00\xff"),
            ("RP2040_ALT", " Raspberry ", "RP2040 / SC0914", "Not Recommended",
             "3", "QFN56", "QFN", "Yes", "unverified alternate",
             "Logic:Base", "Packages:QFN56", None, None),
            ("unknown", None, None, "Nor recommended", "0", None, None, None,
             None, "not a reference", None, None, None),
            ("unknown2", None, None, "None", None, None, None, None,
             None, None, None, None, None),
        ])
        connection.execute('CREATE TABLE "odd""table" (key TEXT PRIMARY KEY, payload BLOB) WITHOUT ROWID')
        connection.execute('INSERT INTO "odd""table" VALUES (?,?)', ("k", b"data"))
        connection.execute('CREATE TABLE "empty table" (x TEXT)')


@pytest.fixture
def database(work):
    path = work / "source #?.sqlite"
    make_database(path)
    return path


@pytest.fixture
def libraries(work):
    root = work / "libraries"
    root.mkdir()
    (root / "SchLib").mkdir()
    (root / "PcbLib/Packages.pretty").mkdir(parents=True)
    (root / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "Logic") (type "KiCad") '
        '(uri "${CERN_LIB_DIR}/SchLib/Logic.kicad_sym")))', encoding="utf-8"
    )
    (root / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "Packages") (type "KiCad") '
        '(uri "${CERN_LIB_DIR}/PcbLib/Packages.pretty")))', encoding="utf-8"
    )
    (root / "SchLib/Logic.kicad_sym").write_text('''(kicad_symbol_lib
      (symbol "Base"
        (symbol "Base_1_1" (pin passive line (number "1")) (pin passive line (number "2")))
        (symbol "Base_1_2" (pin passive line (number "1")) (pin passive line (number "2")))
        (symbol "Base_2_1" (pin power_in line (number "2")) (pin passive line (number "3"))))
      (symbol "Derived" (extends "Base"))
      (symbol "Slash/Name" (extends "Derived"))
      (symbol "Ambiguous"
        (symbol "Ambiguous_1_1" (pin passive line (number "1")))
        (symbol "Ambiguous_1_2" (pin passive line (number "2"))))
      (symbol "CycleA" (extends "CycleB"))
      (symbol "CycleB" (extends "CycleA"))
      (symbol "MissingParent" (extends "NoSuchParent"))
      (symbol "Common"
        (symbol "Common_0_1" (pin power_in line (number "4")))
        (symbol "Common_1_0" (pin passive line (number "1")))
        (symbol "Common_1_1" (pin passive line (number "2")))
        (symbol "Common_1_2" (pin passive line (number "2"))))
    )''', encoding="utf-8")
    (root / "PcbLib/Packages.pretty/QFN56.kicad_mod").write_text('''(footprint "QFN56"
        (pad "1" smd rect) (pad "1" smd rect) (pad "2" smd rect)
        (pad "3" smd rect) (pad "" np_thru_hole circle))''', encoding="utf-8")
    return root


def load_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("filename,definition", [
    ("component_identity.schema.json", "component"),
    ("component_evidence.schema.json", "fieldEvidence"),
    ("component_representation.schema.json", "representation"),
    ("lifecycle_state.schema.json", "lifecycle"),
])
def test_reusable_schema_entrypoints(filename, definition):
    wrapper = json.loads((ROOT / "schemas" / filename).read_text())
    assert wrapper["$ref"] == "cern.schema.json#/$defs/" + definition
    assert definition in SCHEMA["$defs"]


def test_lossless_extraction_and_read_only(database):
    before = sha256(database)
    with open_readonly(database) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden (x)")
        tables = inspect(connection)
        rows = list(extract(connection, tables, before))
        assert len(rows) == 5
        assert len({row["source_id"] for row in rows}) == 5
        for row in rows:
            table = next(table for table in tables if table["name"] == row["source"]["table"])
            assert set(row["fields"]) == {column["name"] for column in table["columns"]}
        assert rows[0]["fields"]["Extra Field"] == {"$sqlite_blob_base64": "AP8="}
        assert rows[-1]["source"]["key"]["primary_key"] == {"key": "k"}
    assert sha256(database) == before
    assert not Path(str(database) + "-wal").exists()
    assert json_value(float("inf")) == {"$sqlite_float": "inf"}


def test_catalog_search_is_read_only_and_bounded(database):
    before = sha256(database)
    results = search_components(database, "rp2040", limit=1)

    assert results["total_matches"] == 2
    assert results["truncated"] is True
    assert len(results["results"]) == 1
    assert results["results"][0]["cern_part_number"] == "RP2040"
    assert results["results"][0]["component"]["manufacturer_part_number"] == "RP2040 / SC0914"
    assert results["evidence"]["verification"] == "unverified"
    assert sha256(database) == before

    with pytest.raises(ValueError, match="non-whitespace"):
        search_components(database, "   ")
    with pytest.raises(ValueError, match="between 1 and 100"):
        search_components(database, "rp2040", limit=101)


def test_shadowed_rowid_and_generated_columns(work):
    database = work / "edge.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE shadow (rowid TEXT, value INTEGER, '
                           'computed INTEGER GENERATED ALWAYS AS (value * 2))')
        connection.execute("INSERT INTO shadow (rowid, value) VALUES ('source rowid', 4)")
        connection.execute("CREATE TABLE literal (x TEXT DEFAULT 'WITHOUT ROWID')")
        connection.execute("INSERT INTO literal DEFAULT VALUES")
        connection.execute('CREATE TABLE all_shadowed (rowid TEXT, _rowid_ TEXT, oid TEXT)')
        connection.execute("INSERT INTO all_shadowed VALUES ('a', 'b', 'c')")
        connection.execute("INSERT INTO all_shadowed VALUES ('a', 'b', 'c')")
    with open_readonly(database) as connection:
        rows = list(extract(connection, inspect(connection), sha256(database)))
    assert len({row["source_id"] for row in rows}) == 4
    by_table = {row["source"]["table"]: row for row in rows}
    assert by_table["literal"]["source"]["key"] == {"rowid": 1}
    assert by_table["shadow"]["source"]["key"] == {"rowid": 1}
    assert by_table["shadow"]["fields"] == {
        "rowid": "source rowid", "value": 4, "computed": 8,
    }


@pytest.mark.parametrize("source,expected,recognized", [
    (None, "unknown", True), ("None", "unknown", True),
    (" Not recommended ", "not_recommended", True),
    ("Not Recommended for New Designs", "not_recommended", True),
    ("End Of Life", "end_of_life", True), ("Sourcing Difficulty", "sourcing_difficulty", True),
    ("Nor recommended", "unknown", False), ("Not Recommended Not Realesed", "unknown", False),
    ("Not Preferred", "not_preferred", True), ("Preliminary", "preliminary", True),
])
def test_lifecycle_source_preserved(source, expected, recognized):
    status = normalize_status(source)
    assert status["source_value"] == source
    assert status["normalized"] == expected
    assert status["recognized"] is recognized


def test_identity_is_not_representation(database):
    with open_readonly(database) as connection:
        rows = list(extract(connection, inspect(connection), sha256(database)))
    identities = [normalize_identity(row) for row in rows]
    assert identities[0]["component_id"] == identities[1]["component_id"]
    assert identities[2]["component_id"] != identities[3]["component_id"]
    other = copy.deepcopy(rows[0])
    other["fields"]["Manufacturer Part Number"] = "rp2040 / SC0914"
    assert normalize_identity(other)["component_id"] != identities[0]["component_id"]


def test_symbol_geometry_units_alternates_inheritance(libraries):
    joiner = SymbolJoiner(Libraries(libraries))
    for name in ("Base", "Derived", "Slash/Name"):
        geometry = joiner.join(parse_reference("Logic:" + name))
        assert geometry["status"] == "resolved"
        assert geometry["physical_numbers"] == ["1", "2", "3"]
        assert geometry["count_comparable"]
    geometry = joiner.join(parse_reference("Logic:Ambiguous"))
    assert not geometry["count_comparable"]
    assert geometry["ambiguities"] == ["alternate_pin_number_sets_differ"]
    assert joiner.join(parse_reference("Logic:Common"))["physical_numbers"] == ["1", "2", "4"]
    for name in ("CycleA", "MissingParent"):
        assert joiner.join(parse_reference("Logic:" + name))["status"] == "parse_error"
    assert joiner.join(parse_reference("Logic:Absent"))["status"] == "symbol_not_found"


def test_footprint_unique_pads_and_mechanical_holes(libraries):
    joiner = FootprintJoiner(Libraries(libraries))
    geometry = joiner.join(parse_reference("Packages:QFN56"))
    assert geometry["physical_count"] == 3
    assert geometry["unnumbered_mechanical_pads"] == 1
    assert geometry["mounting"] == "smd"
    assert geometry["count_comparable"]
    (libraries / "PcbLib/Packages.pretty/Unknown.kicad_mod").write_text(
        '(footprint "Unknown" (pad "1" thru_hole circle) (pad "" smd rect))'
    )
    geometry = joiner.join(parse_reference("Packages:Unknown"))
    assert not geometry["count_comparable"]
    assert geometry["mounting"] == "mixed_or_unknown"


def test_reference_and_library_paths_are_safe(libraries, work):
    libs = Libraries(libraries)
    for value in ("Packages:../../source", "Packages:/etc/passwd", "Packages:..\\source"):
        assert libs.resolve("footprint", parse_reference(value))[1] == "unsafe_reference"
    assert parse_reference("Logic:Slash/Name")["status"] == "parsed"
    assert parse_reference("Logic:with:colon")["name"] == "with:colon"
    assert parse_reference("bare")["status"] == "malformed"
    assert parse_reference(None)["status"] == "missing"
    libs.tables["symbol"]["Logic"]["uri"] = "${CERN_LIB_DIR}/../outside.kicad_sym"
    assert libs.resolve("symbol", parse_reference("Logic:Base"))[1] == "unsafe_library_path"
    outside = work / "outside.kicad_sym"
    outside.write_text("(kicad_symbol_lib)")
    symbol = libraries / "SchLib/Logic.kicad_sym"
    symbol.unlink()
    symbol.symlink_to(outside)
    assert Libraries(libraries).resolve("symbol", parse_reference("Logic:Base"))[1] == "unsafe_library_path"
    table = libraries / "sym-lib-table"
    table.unlink()
    table.symlink_to(outside)
    assert "symbol" in Libraries(libraries).table_errors


def test_sexpression_rejects_corruption():
    assert parse(r'(x "a\"b" "a\\b")') == ["x", 'a"b', "a\\b"]
    for content in ('(x', '(x))', '(x "unterminated)', '(a)(b)'):
        with pytest.raises(ValueError):
            parse(content)


def test_end_to_end_contracts_and_determinism(database, libraries, work):
    output = work / "output"
    before = sha256(database)
    report = ingest(database, output, libraries)
    assert all(value for key, value in report["checks"].items() if key != "geometry_complete")
    assert report["counts"]["raw_rows"] == 5
    assert report["counts"]["normalized_components"] == 4
    assert report["counts"]["representations"] == 10
    assert report["anomaly_counts"]["semantic_generation_mismatch"] == 1
    assert report["anomaly_counts"]["pin_count_mismatch"] == 0
    assert report["anomaly_counts"]["identity_collision"] == 0
    fingerprints = {name: sha256(output / name) for name in OUTPUTS}
    ingest(database, output, libraries)
    assert fingerprints == {name: sha256(output / name) for name in OUTPUTS}
    assert sha256(database) == before
    for filename, (definition, wrapper_name) in CONTRACTS.items():
        wrapper = json.loads((ROOT / "schemas" / wrapper_name).read_text())
        assert wrapper["$ref"] == "cern.schema.json#/$defs/" + definition
        values = load_lines(output / filename) if filename.endswith(".jsonl") else [
            json.loads((output / filename).read_text())
        ]
        for value in values:
            validate(value, SCHEMA["$defs"][definition])
    components = load_lines(output / "components.normalized.jsonl")
    rp2040 = next(component for component in components if len(component["source_ids"]) == 2)
    assert len(rp2040["representation_ids"]) == 4
    assert rp2040["cern_part_numbers"] == ["RP2040", "RP2040_ALT"]
    raw = {row["source_id"]: row for row in load_lines(output / "components.raw.jsonl")}
    for component in components:
        assert len(component["field_evidence"]) == sum(
            len(raw[source_id]["fields"]) for source_id in component["source_ids"]
        )
        for evidence in component["field_evidence"]:
            assert evidence["source_value"] == raw[evidence["source_id"]]["fields"][evidence["field"]]
            assert evidence["claim"] == "source_assertion"
            assert evidence["confidence"] == evidence["verification_state"] == "unverified"
    manufacturer = [item for item in rp2040["field_evidence"] if item["field"] == "Manufacturer"]
    assert {item["source_value"] for item in manufacturer} == {"RASPBERRY", " Raspberry "}
    assert {item["normalized_value"] for item in manufacturer} == {"raspberry"}
    assert {s["source_value"] for s in rp2040["lifecycle"]} == {None, "Not Recommended"}
    assert report["top_100_anomalies"] == sorted(
        load_lines(output / "anomalies.jsonl"), key=anomaly_order
    )[:100]


def test_schema_rejects_false_verification_and_incomplete_geometry(database, libraries, work):
    ingest(database, work / "output", libraries)
    rep = load_lines(work / "output/representations.jsonl")
    resolved = next(item for item in rep if item["geometry"]["status"] == "resolved")
    broken = copy.deepcopy(resolved)
    broken["evidence"]["verification"] = "verified"
    with pytest.raises(AssertionError):
        validate(broken, SCHEMA["$defs"]["representation"])
    evidence = load_lines(work / "output/components.normalized.jsonl")[0]["field_evidence"][0]
    for field, value in (("verification_state", "verified"), ("confidence", "high")):
        broken = {**evidence, field: value}
        with pytest.raises(AssertionError):
            validate(broken, SCHEMA["$defs"]["fieldEvidence"])
    broken = copy.deepcopy(resolved)
    del broken["geometry"]["physical_count"]
    with pytest.raises(AssertionError):
        validate(broken, SCHEMA["$defs"]["representation"])
    broken = copy.deepcopy(resolved)
    broken["component_id"] = "not-an-id"
    with pytest.raises(AssertionError):
        validate(broken, SCHEMA["$defs"]["representation"])


def test_offline_reports_incomplete_geometry(database, work):
    report = ingest(database, work / "offline")
    assert not report["checks"]["geometry_complete"]
    assert report["counts"]["unresolved_representations"] == 10
    assert report["datasheet_crosscheck"] == "not_implemented"
    result = subprocess.run(
        [sys.executable, "-m", "adapters.cern", "--sqlite", str(database),
         "--output", str(work / "cli"), "--require-complete-geometry"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert (work / "cli/quality_report.json").is_file()


def test_output_aliases_cannot_overwrite_evidence(database, libraries, work):
    with pytest.raises(ValueError):
        ingest(database, database)
    with pytest.raises(ValueError):
        ingest(database, libraries / "output", libraries)
    output = work / "unsafe"
    output.mkdir()
    (output / "components.raw.jsonl").symlink_to(database)
    before = sha256(database)
    with pytest.raises(ValueError):
        ingest(database, output)
    assert sha256(database) == before


def test_active_wal_is_rejected(database, work):
    wal = Path(str(database) + "-wal")
    wal.write_bytes(b"not a stable standalone snapshot")
    with pytest.raises(ValueError, match="WAL"):
        ingest(database, work / "output")


def test_anomaly_rules_and_rp2040_golden_negative():
    row = {
        "source_id": "s1", "source": {"table": "Logic"},
        "fields": {
            "Part Number": "RP2040", "Pin Count": "57", "Case": "QFN56",
            "PackageDescription": "QFN", "SMD": "Yes",
            "Part Description": "M33, Hazard3, 150MHz, 520KB SRAM, 2 MB Internal Flash",
        },
    }
    reps = list(representations(row, "c"))
    reps[0]["geometry"] = {"status": "resolved", "count_comparable": True, "physical_count": 57}
    reps[1]["geometry"] = {
        "status": "resolved", "count_comparable": True, "physical_count": 57, "mounting": "smd",
    }
    findings = list(row_anomalies(row, "c", reps))
    assert [finding["rule"] for finding in findings] == ["semantic_generation_mismatch"]
    assert len(findings[0]["evidence"]["conflicting_claims"]) == 5
    row["fields"]["Part Description"] = "Dual Cortex-M0+, 133MHz, 264KB SRAM, external flash"
    assert not list(row_anomalies(row, "c", reps))
    row["fields"]["Part Description"] = "Dual Cortex-M0+, 133MHz, no internal flash"
    assert not list(row_anomalies(row, "c", reps))
    row["fields"].update({"Part Number": "Module RP2040", "Part Description": "150MHz M33"})
    assert not list(row_anomalies(row, "c", reps))
    reps[1]["geometry"].update({"physical_count": 56, "mounting": "through_hole"})
    reps[1]["reference"]["name"] = "SOIC56"
    assert {item["rule"] for item in row_anomalies(row, "c", reps)} == {
        "pin_count_mismatch", "package_mismatch", "mounting_mismatch",
    }
    reps[1]["geometry"]["count_comparable"] = False
    assert "pin_count_mismatch" not in {item["rule"] for item in row_anomalies(row, "c", reps)}


def test_identity_collisions_are_families_not_mounting_variants():
    rows = {
        "a": {"source": {"table": "Capacitors SMD"}},
        "b": {"source": {"table": "Capacitors THD"}},
    }
    components = [{"component_id": "c", "source_ids": ["a", "b"]}]
    assert not list(collision_anomalies(components, rows))
    rows["b"]["source"]["table"] = "Resistors SMD"
    assert len(list(collision_anomalies(components, rows))) == 1


def test_package_families_do_not_guess_ambiguous_descriptions():
    assert package_family("QFN56 or SOIC56") is None
    assert package_family("DIP8 / DIL8") == "DIP"
    assert package_family("0603") is None


def test_real_sqlite_all_rows_preserved_and_rp2040_detected(work):
    database = ROOT / "CERN.sqlite"
    if not database.is_file():
        pytest.skip("CERN.sqlite not provided")
    before = sha256(database)
    output = work / "real"
    report = ingest(database, output)
    assert report["counts"]["tables"] == 55
    assert report["checks"]["all_rows_extracted"]
    raw = load_lines(output / "components.raw.jsonl")
    with open_readonly(database) as connection:
        tables = inspect(connection)
        for table in tables:
            cursor = connection.execute(f"SELECT * FROM {quote(table['name'])} ORDER BY rowid")
            columns = [column[0] for column in cursor.description]
            expected = [dict(zip(columns, map(json_value, row))) for row in cursor]
            actual = [row["fields"] for row in raw if row["source"]["table"] == table["name"]]
            assert actual == expected
    assert len(raw) == sum(table["row_count"] for table in tables)
    rp2040 = next(row for row in raw if row["source"]["table"] == "Logic"
                  and row["fields"].get("Part Number") == "RP2040")
    findings = load_lines(output / "anomalies.jsonl")
    assert any(item["rule"] == "semantic_generation_mismatch"
               and rp2040["source_id"] in item["source_ids"] for item in findings)
    assert report["top_100_anomalies"][0]["rule"] == "semantic_generation_mismatch"
    assert sha256(database) == before
