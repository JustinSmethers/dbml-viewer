"""Warehouse layer classification, mirroring DBMLLayout.stages in templates/layout.js.

The browser classifies tables for placement; Python classifies them for
review rules (required charter keys, naming, layer violations). Both must
agree, so tests/test_layers.py checks parity on the example warehouse.
"""

import re

LAYERS = ["source", "seed", "prep", "intermediate", "bridge", "mart"]

_ALIASES = [
    re.compile(r"^(sources?|src|raw|snapshots?|external|landing)$"),
    re.compile(r"^seeds?$"),
    re.compile(r"^(prep(?:are|ared|aration|ped)?|stg|stag(?:e|ing))$"),
    re.compile(r"^(int|inter|intermed|intermediary|intermediates?|integration|integrated|transform(?:s|ations?)?)$"),
    re.compile(r"^bridges?$"),
    re.compile(r"^(marts?|reports?|reporting|rpt|dim(?:s|ension|ensions)?|facts?|fct|analytics|metrics?|presentation)$"),
]
_ROLES = {0, 1, 4}  # source, seed, bridge stay distinct inside broader groups


def _token_stage(text: str) -> int | None:
    tokens = re.split(r"[^a-z0-9]+", text)
    if any(_ALIASES[4].match(token) for token in tokens):
        return 4
    for token in tokens:
        for index, pattern in enumerate(_ALIASES):
            if pattern.match(token):
                return index
    return None


def _stage(name: str | None) -> int | None:
    """Classify the bare name (after the last ``.``) first, so ``analytics.stg_x`` is prep."""
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name or "")).lower()
    bare = text.rsplit(".", 1)[-1]
    stage = _token_stage(bare)
    return stage if stage is not None or bare == text else _token_stage(text)


def _table_stage(name: str, group: str | None) -> int | None:
    group_stage, table_stage = _stage(group), _stage(name)
    if table_stage in _ROLES:
        return table_stage
    return group_stage if group_stage is not None else table_stage


def layer_of(name: str, group: str | None = None) -> str | None:
    """Layer from a table's own name and group only (no neighbour inference)."""
    stage = _table_stage(name, group)
    return LAYERS[stage] if stage is not None else None


def classify(data: dict) -> dict[str, str | None]:
    """Return {table_name: layer} using the same rules as the diagram layout.

    Unrecognised tables inherit a processing layer (prep/intermediate/mart)
    from connected tables; disconnected unknowns stay ``None`` and callers
    should treat them as intermediate for placement but skip layer rules.
    """
    result: dict[str, int | None] = {}
    for table in data["tables"]:
        result[table["name"]] = _table_stage(table["name"], table.get("group"))
    for _ in range(len(data["tables"])):
        updates = {}
        for table in data["tables"]:
            name = table["name"]
            if result[name] is not None:
                continue
            neighbours = []
            for ref in data.get("refs", []):
                other = ref["to_table"] if ref["from_table"] == name else ref["from_table"] if ref["to_table"] == name else None
                if other and result.get(other) is not None:
                    stage = result[other]
                    neighbours.append(0 if stage <= 2 else 1 if stage == 3 else 2)
            if neighbours:
                inferred = int(round(sum(neighbours) / len(neighbours) + 1e-9))
                updates[name] = [2, 3, 5][inferred]
        if not updates:
            break
        result.update(updates)
    return {name: (LAYERS[stage] if stage is not None else None) for name, stage in result.items()}
