"""Lifecycle vocabulary: preserve source text, do not infer availability."""


STATUS = {
    "preferred": "preferred",
    "recommended": "recommended",
    "not preferred": "not_preferred",
    "not recommended": "not_recommended",
    "not recommended for new designs": "not_recommended",
    "obsolete": "obsolete",
    "preliminary": "preliminary",
    "discontinued": "discontinued",
    "end of life": "end_of_life",
    "sourcing difficulty": "sourcing_difficulty",
}


def normalize_status(value):
    token = " ".join(str(value).split()).casefold() if value is not None else ""
    canonical = STATUS.get(token, "unknown")
    return {
        "source_value": value,
        "normalized": canonical,
        "recognized": token in STATUS or token in ("", "none"),
        "normalization_needed": token not in STATUS and token not in ("", "none"),
    }
