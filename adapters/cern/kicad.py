"""Small S-expression reader and bounded library-table resolver; no KiCad dependency."""

import re
from functools import lru_cache
from pathlib import Path

from .inspect_sqlite import sha256

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')


def parse(content):
    stack = [[]]
    position = 0
    for token in TOKEN.finditer(content):
        if content[position:token.start()].strip():
            raise ValueError("invalid S-expression token")
        position = token.end()
        value = token.group()
        if value == "(":
            child = []
            stack[-1].append(child)
            stack.append(child)
        elif value == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced S-expression")
            stack.pop()
        elif value.startswith('"'):
            stack[-1].append(re.sub(r'\\(["\\])', r'\1', value[1:-1]))
        else:
            stack[-1].append(value)
    if len(stack) != 1 or content[position:].strip() or len(stack[0]) != 1:
        raise ValueError("unbalanced S-expression")
    return stack[0][0]


def children(node, tag):
    return [item for item in node if isinstance(item, list) and item and item[0] == tag]


def field(node, tag, default=None):
    found = children(node, tag)
    return found[0][1] if found and len(found[0]) > 1 else default


def descendants(node, tag):
    for item in node:
        if isinstance(item, list):
            if item and item[0] == tag:
                yield item
            yield from descendants(item, tag)


class Libraries:
    def __init__(self, root=None):
        self.root = Path(root).resolve(strict=True) if root else None
        self.tables = {}
        self.table_errors = {}
        self.table_hashes = {}
        for kind, filename in (("symbol", "sym-lib-table"), ("footprint", "fp-lib-table")):
            mapping = {}
            if self.root:
                try:
                    path = self.relative(self.root / filename)
                    tree = self.read(path)
                    expected = "sym_lib_table" if kind == "symbol" else "fp_lib_table"
                    if not tree or tree[0] != expected:
                        raise ValueError("unexpected library table format")
                    self.table_hashes[filename] = sha256(path)
                    for entry in children(tree, "lib"):
                        name = field(entry, "name")
                        if name in mapping:
                            raise ValueError("duplicate library nickname")
                        mapping[name] = {"uri": field(entry, "uri"), "type": field(entry, "type")}
                except (OSError, ValueError) as error:
                    self.table_errors[kind] = str(error)
                    mapping = {}
            self.tables[kind] = mapping

    @lru_cache(maxsize=128)
    def read(self, path):
        return parse(path.read_text(encoding="utf-8-sig"))

    def relative(self, path):
        path = Path(path).resolve()
        path.relative_to(self.root)
        return path

    def resolve(self, kind, reference):
        if reference["status"] != "parsed":
            return None, reference["status"]
        if kind == "footprint" and (
            reference["name"] in (".", "..")
            or any(char in reference["name"] for char in "/\\")
        ):
            return None, "unsafe_reference"
        if self.root is None:
            return None, "no_library_checkout"
        if kind in self.table_errors:
            return None, "library_table_error"
        entry = self.tables[kind].get(reference["library"])
        if entry is None:
            return None, "library_not_found"
        if entry["type"] != "KiCad":
            return None, "unsupported_library_type"
        uri = entry["uri"] or ""
        prefix = "${CERN_LIB_DIR}/"
        if not uri.startswith(prefix) or "${" in uri[len(prefix):]:
            return None, "unsupported_library_uri"
        relative = uri[len(prefix):]
        if "\\" in relative or any(part == ".." for part in relative.split("/")):
            return None, "unsafe_library_path"
        try:
            path = self.relative(self.root / relative)
            if kind == "footprint":
                path = self.relative(path / (reference["name"] + ".kicad_mod"))
        except ValueError:
            return None, "unsafe_library_path"
        if not path.is_file():
            return None, "file_not_found"
        return path, "resolved"

    def provenance(self, path):
        return {"path": path.relative_to(self.root).as_posix(), "sha256": self.digest(path)}

    @lru_cache(maxsize=None)
    def digest(self, path):
        return sha256(path)
