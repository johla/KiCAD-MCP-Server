"""Count unique numbered electrical pads, not shape instances or mounting holes."""

from functools import lru_cache

from .kicad import children


class FootprintJoiner:
    def __init__(self, libraries):
        self.libraries = libraries

    @lru_cache(maxsize=None)
    def geometry(self, path):
        tree = self.libraries.read(path)
        if not tree or tree[0] not in ("footprint", "module"):
            raise ValueError("not a KiCad footprint")
        numbers = set()
        types = set()
        issues = []
        mechanical = 0
        for pad in children(tree, "pad"):
            if len(pad) < 3:
                raise ValueError("malformed pad")
            number, kind = pad[1:3]
            if kind == "np_thru_hole":
                mechanical += not number
                continue
            if kind not in ("smd", "thru_hole", "connect"):
                issues.append("unsupported_pad_type")
            types.add(kind)
            if number:
                numbers.add(number)
            else:
                issues.append("unnumbered_electrical_pads")
        if not numbers:
            issues.append("no_numbered_pads")
        mounting = "mixed_or_unknown"
        if types == {"smd"}:
            mounting = "smd"
        elif types == {"thru_hole"}:
            mounting = "through_hole"
        return {
            "status": "resolved",
            "provenance": self.libraries.provenance(path),
            "physical_numbers": sorted(numbers),
            "physical_count": len(numbers),
            "count_comparable": not issues,
            "ambiguities": sorted(set(issues)),
            "unnumbered_mechanical_pads": mechanical,
            "mounting": mounting,
        }

    def join(self, reference):
        path, status = self.libraries.resolve("footprint", reference)
        if path is None:
            return {"status": status}
        try:
            return self.geometry(path)
        except (OSError, ValueError, IndexError, RecursionError) as error:
            return {"status": "parse_error", "detail": str(error)}
