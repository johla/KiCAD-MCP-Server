"""Join actual symbol geometry, handling inheritance, units and alternate bodies."""

import re
from functools import lru_cache

from .kicad import children, descendants, field


class SymbolJoiner:
    def __init__(self, libraries):
        self.libraries = libraries

    @lru_cache(maxsize=128)
    def index(self, path):
        tree = self.libraries.read(path)
        if not tree or tree[0] != "kicad_symbol_lib":
            raise ValueError("not a KiCad symbol library")
        result = {}
        for symbol in children(tree, "symbol"):
            if symbol[1] in result:
                raise ValueError("duplicate symbol name")
            result[symbol[1]] = symbol
        return result

    def pins(self, symbols, name, seen=()):
        if name in seen:
            raise ValueError("cyclic symbol inheritance")
        if name not in symbols:
            raise ValueError("missing inherited symbol")
        symbol = symbols[name]
        own = list(descendants(symbol, "pin"))
        parent = field(symbol, "extends")
        if parent:
            inherited, issues = self.pins(symbols, parent, seen + (name,))
            if not own:
                return inherited, issues
            return inherited | {field(pin, "number", "") for pin in own}, (
                issues + ["inherited_and_local_pins"]
            )
        groups = {}
        issues = []
        for body in children(symbol, "symbol"):
            match = re.search(r"_(\d+)_(\d+)$", body[1])
            if not match:
                issues.append("unknown_symbol_unit")
                continue
            unit, style = match.groups()
            if style in groups.get(unit, {}):
                issues.append("duplicate_symbol_unit")
            groups.setdefault(unit, {})[style] = {
                field(pin, "number", "") for pin in children(body, "pin")
            }
        pins = set()
        for styles in groups.values():
            common = styles.get("0", set())
            alternatives = [common | numbers for style, numbers in styles.items() if style != "0"]
            if len(alternatives) > 1 and any(s != alternatives[0] for s in alternatives[1:]):
                issues.append("alternate_pin_number_sets_differ")
            for numbers in styles.values():
                pins.update(numbers)
        if "" in pins or "~" in pins:
            issues.append("unnumbered_symbol_pins")
            pins.discard("")
            pins.discard("~")
        if not pins:
            issues.append("no_numbered_pins")
        if len(own) and not groups:
            issues.append("unsupported_pin_layout")
        return pins, sorted(set(issues))

    @lru_cache(maxsize=None)
    def geometry(self, path, name):
        symbols = self.index(path)
        if name not in symbols:
            return {"status": "symbol_not_found"}
        pins, issues = self.pins(symbols, name)
        return {
            "status": "resolved",
            "provenance": self.libraries.provenance(path),
            "physical_numbers": sorted(pins),
            "physical_count": len(pins),
            "count_comparable": not issues,
            "ambiguities": issues,
        }

    def join(self, reference):
        path, status = self.libraries.resolve("symbol", reference)
        if path is None:
            return {"status": status}
        try:
            return self.geometry(path, reference["name"])
        except (OSError, ValueError, IndexError, RecursionError) as error:
            return {"status": "parse_error", "detail": str(error)}
