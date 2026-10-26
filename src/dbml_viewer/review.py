"""Assemble layers, lineage, charter findings, overlap, walkthrough, and bus matrix.

``build`` is the single entry point used by the CLI and the demo. It writes
``data["layers"]``, ``data["lineage"]`` and ``data["review"]`` together at the
end, so a failure never leaves a partial review in the data.
"""

import re
from pathlib import Path

from dbml_viewer import charter, lineage
from dbml_viewer.charter import _charter
from dbml_viewer.layers import classify, layer_of

SEVERITY = {"error": 0, "warning": 1, "info": 2}
PRESENTATION = ("mart", "bridge")
GENERIC_COLUMNS = {"id", "created_at", "updated_at", "_loaded_at", "loaded_at", "inserted_at"}
DIM_TOKENS = {"dim", "dims", "dimension", "dimensions"}


def build(data: dict, *, comparison: dict | None = None, root: Path | str | None = None,
          model_dirs: list[str] | None = None, manifests: list[str] | None = None) -> None:
    """Set data["layers"], data["lineage"] and data["review"]."""
    layers = classify(data)
    graph = lineage.build(data, layers, root=root, model_dirs=model_dirs, manifests=manifests)
    known_models = {name for name, kind in graph["stub_kinds"].items() if kind == "model"}
    names = [table["name"] for table in data["tables"]]
    table_names = set(names)
    by_name = {table["name"]: table for table in data["tables"]}
    # Path-keyed (unmapped) sources stay in the comparison; the review covers models only.
    compared = {name: entry for name, entry in ((comparison or {}).get("tables") or {}).items()
                if not entry.get("unmapped")}
    statuses = {name: entry.get("status") for name, entry in compared.items()}
    charter_findings = charter.findings(data, layers, comparison, known_models)
    lineage_findings = lineage.findings(data, layers, graph, statuses)
    charters = {name: _charter(by_name[name]) for name in names}
    # Declared replacements resolve to a diagram table, then to a compared model (e.g. a removed one).
    replaces = {name: [charter.resolve(item, table_names) or charter.resolve(item, set(compared)) or item
                       for item in lineage._names(charters[name].get("replaces"))]
                for name in names}
    replaced_by = {}
    for name in names:
        for target in replaces[name]:
            replaced_by.setdefault(target, []).append(name)

    tables = {}
    for name in names:
        layer = layers.get(name)
        merged = list(charter_findings.get(name, [])) + list(lineage_findings.get(name, []))
        merged.sort(key=lambda item: SEVERITY.get(item.get("severity"), len(SEVERITY)))
        required, missing = charter.completeness(by_name[name], layer)
        new_presentation = statuses.get(name) == "new" and layer in PRESENTATION
        tables[name] = {
            "layer": layer,
            "findings": merged,
            "required": list(required),
            "missing": list(missing),
            "replaces": replaces[name],
            "replaced_by": replaced_by.get(name, []),
            "overlap": _overlap(name, by_name, layers, graph, statuses, charters, replaces) if new_presentation else [],
        }
    # Removed models have no card; classify them from their baseline design so the panel can place them.
    for name, entry in compared.items():
        if entry.get("status") == "removed" and name not in tables:
            baseline = entry.get("baseline") or {}
            tables[name] = {"layer": layer_of(name, baseline.get("group")), "findings": [], "required": [],
                            "missing": [], "replaces": [], "replaced_by": replaced_by.get(name, []), "overlap": []}

    counts = {severity: 0 for severity in SEVERITY}
    for entry in tables.values():
        for item in entry["findings"]:
            if item.get("severity") in counts:
                counts[item["severity"]] += 1
    review = {
        "tables": tables,
        "walkthrough": _walkthrough(graph["order"], statuses, replaces, table_names),
        "bus_matrix": bus_matrix(data, layers),
        "counts": counts,
    }
    data["layers"], data["lineage"], data["review"] = layers, graph, review


def _overlap(name, by_name, layers, graph, statuses, charters, replaces) -> list[dict]:
    """Rank existing marts/bridges a new mart could extend instead of being created.

    Models the new mart declares in ``replaces`` are linked separately, so they
    are not candidates, and candidates are the same kind: a new dimension is
    compared with dimensions only, anything else with non-dimensions only.
    Ties go to the same grain, then more shared upstreams, then the name.
    """
    upstream = set(graph["tables"][name]["upstream"])
    grain = {column.casefold() for column in lineage._names(charters[name].get("grain"))}
    columns = {column["name"].casefold() for column in by_name[name].get("columns", [])} - GENERIC_COLUMNS
    dimension = _has_dim_token(name)
    metrics = _metric_names(charters[name])
    candidates = [other for other in by_name if other != name and other not in replaces[name]
                  and layers.get(other) in PRESENTATION and statuses.get(other) not in ("new", "removed")
                  and _has_dim_token(other) == dimension]
    ranked = []
    for other in candidates:
        score, reasons = 0, []
        duplicated = sorted(metrics.keys() & _metric_names(charters[other]).keys())
        if duplicated:
            score += 3
            label = "metric" if len(duplicated) == 1 else "metrics"
            reasons.append(f"Owns the duplicated {label} {', '.join(f'`{metrics[key]}`' for key in duplicated)} (+3)")
        shared = sorted(upstream & set(graph["tables"][other]["upstream"]))
        if shared:
            score += 2 * len(shared)
            reasons.append(f"Shares upstream {', '.join(shared)} (+{2 * len(shared)})")
        theirs = {column.casefold() for column in lineage._names(charters[other].get("grain"))}
        same_grain = bool(grain) and grain == theirs
        if same_grain:
            score += 3
            reasons.append(f"Same grain ({', '.join(sorted(grain))}) (+3)")
        elif grain and theirs and (grain < theirs or theirs < grain):
            score += 1
            reasons.append(f"Compatible grain: ({', '.join(sorted(grain))}) vs ({', '.join(sorted(theirs))}) (+1)")
        common = sorted(columns & {column["name"].casefold() for column in by_name[other].get("columns", [])})
        if common:
            points = min(3, len(common))
            score += points
            reasons.append(f"Supporting evidence only: {len(common)} shared column name(s): {', '.join(common)} (+{points})")
        if score:
            ranked.append(((-score, not same_grain, -len(shared), other), {"model": other, "score": score, "reasons": reasons}))
    ranked.sort(key=lambda item: item[0])
    return [item for _, item in ranked[:3]]


def _has_dim_token(name: str) -> bool:
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", name.rsplit(".", 1)[-1]).lower()
    return bool(DIM_TOKENS & set(re.split(r"[^a-z0-9]+", text)))


def _metric_names(charter_data: dict) -> dict[str, str]:
    """Case-folded metric name -> declared name."""
    names = {}
    for metric in charter_data.get("metrics") or []:
        value = metric.get("name") if isinstance(metric, dict) else None
        if isinstance(value, str) and value.strip():
            names.setdefault(value.strip().casefold(), value.strip())
    return names


def _walkthrough(order, statuses, replaces, table_names) -> list[str]:
    """Changed models in lineage order, each preceded by the models it replaces.

    Changed comparison entries without a diagram card (removed models) are
    included too: a removed model goes right before a model that replaces it,
    and anything else goes at the end, by name. Unmapped, path-keyed sources
    are never steps; ``statuses`` excludes them.
    """
    steps = []
    for name in order:
        if statuses.get(name, "unchanged") == "unchanged":
            continue
        steps.extend(target for target in replaces.get(name, [])
                     if (target in table_names or target in statuses) and target not in steps)
        if name not in steps:
            steps.append(name)
    cardless = sorted(name for name, status in statuses.items()
                      if status != "unchanged" and name not in table_names and name not in steps)
    for name in cardless:
        replacer = next((step for step in steps if name in replaces.get(step, [])), None)
        if replacer:
            steps.insert(steps.index(replacer), name)
        else:
            steps.append(name)
    return steps


def _is_dimension(name: str, layers: dict) -> bool:
    return _has_dim_token(name) and layers.get(name) not in ("source", "seed", "prep", "intermediate")


def bus_matrix(data: dict, layers: dict) -> dict:
    """Facts x conformed dimensions, from declared DBML refs only (never column-name matching)."""
    names = {table["name"] for table in data["tables"]}
    dims = sorted(name for name in names if _is_dimension(name, layers))
    cells: dict[str, set[str]] = {}
    for ref in data.get("refs", []):
        pairs = [(ref["from_table"], ref["to_table"])]
        if ref.get("type") == "-":  # one-to-one refs have no foreign-key side
            pairs.append((ref["to_table"], ref["from_table"]))
        for fact, dim in pairs:
            if fact in names and dim in dims and fact not in dims and layers.get(fact) in PRESENTATION:
                cells.setdefault(fact, set()).add(dim)
    facts = sorted(cells)
    return {"dims": dims, "facts": facts, "cells": {fact: sorted(cells[fact]) for fact in facts}}
