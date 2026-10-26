"""Model charter: structured review metadata embedded in DBML table notes.

A charter is a YAML mapping between two lines that are exactly ``---`` inside
a table ``Note``. Text outside the block stays prose. See
docs/charter-convention.md for the convention and the rules below.
"""

import datetime
import difflib
import re
from pathlib import PurePosixPath

import yaml

KNOWN_KEYS = (
    "grain", "purpose", "questions", "consumers", "owner", "replaces", "depends_on",
    "metrics", "history", "materialization", "freshness", "pii", "validation",
)
REQUIRED_KEYS = {
    "mart": ["grain", "purpose", "consumers", "owner", "replaces"],
    "bridge": ["grain", "purpose", "consumers", "owner", "replaces"],
    "intermediate": ["grain", "purpose"],
    "prep": ["grain"],
}
NAMING_PREFIXES = {
    "mart": ("fct_", "dim_", "mart_", "marts_", "rpt_", "report_", "reports_"),
    "intermediate": ("int_", "inter_", "intermediate_"),
    "prep": ("stg_", "prep_", "prepared_"),
}
LAYER_LABELS = {"mart": "Mart", "bridge": "Bridge", "intermediate": "Intermediate", "prep": "Prep"}
HISTORY_TOKENS = ("valid_from", "valid_to", "effective", "version", "snapshot", "_at", "date")

# Keys whose values are lists of names; a comma-separated string is split.
_NAME_LISTS = ("grain", "replaces", "depends_on", "pii")
# Keys whose values are lists of free text; a single string becomes one item.
_TEXT_LISTS = ("consumers", "questions")


def _is_delimiter(line: str) -> bool:
    return line.strip() == "---"


# Charter keys are identifiers; prose such as "Daily rollup: per customer" is not.
_KEY = re.compile(r"^[\w-]+$")


def _content(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _looks_like_mapping(lines: list[str]) -> bool:
    content = _content(lines)
    return bool(content) and bool(re.match(r"^[\w-]+\s*:(\s|$)", content[0]))


def extract(note: str) -> tuple[str, dict | None, str | None]:
    """Split a table note into (prose, charter dict or None, parse error or None).

    The charter is the first YAML mapping between two consecutive ``---`` lines.
    Markdown rules around prose, a lone or unclosed ``---``, and blocks that are
    not a mapping stay prose with no error. Only a block that looks like a
    mapping (its first line is ``key:``) but cannot be parsed is an error.
    """
    note = note or ""
    lines = note.splitlines()
    marks = [i for i, line in enumerate(lines) if _is_delimiter(line)]
    for start, end in zip(marks, marks[1:]):
        body = lines[start + 1:end]
        try:
            loaded = yaml.safe_load("\n".join(body))
            if loaded is None and not _content(body):
                loaded = {}
            if not (isinstance(loaded, dict) and all(isinstance(k, str) and _KEY.match(k) for k in loaded)):
                continue
            charter = _normalize(loaded)
        except Exception as exc:  # noqa: BLE001 - also RecursionError from recursive anchors
            if not _looks_like_mapping(body):
                continue
            mark = getattr(exc, "problem_mark", None)
            where = f" near line {mark.line + 1} of the block" if mark else ""
            problem = getattr(exc, "problem", None) or (str(exc).splitlines() or [type(exc).__name__])[0]
            return note, None, f"Charter YAML could not be parsed{where}: {problem}."
        prose = "\n".join(lines[:start] + lines[end + 1:]).strip()
        return prose, charter, None
    return note, None, None


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _as_list(value, split_commas: bool) -> list | None:
    if value is None:
        return None
    if isinstance(value, str):
        if split_commas:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value] if value.strip() else []
    if not isinstance(value, list):
        value = [value]
    if split_commas:
        # Name lists hold column/model names only; mappings and nested lists are dropped.
        return [str(item) for item in value if item is not None and not isinstance(item, (dict, list))]
    return [item if isinstance(item, (dict, list)) else str(item) for item in value]


def _normalize(raw: dict) -> dict:
    """Coerce scalars to lists where the schema expects lists; keep unknown keys."""
    charter = _json_safe(raw)
    for key in _NAME_LISTS + _TEXT_LISTS:
        if key in charter:
            charter[key] = _as_list(charter[key], key in _NAME_LISTS)
    if isinstance(charter.get("materialization"), str):
        charter["materialization"] = {"type": charter["materialization"]}
    if isinstance(charter.get("materialization"), dict) and "unique_key" in charter["materialization"]:
        charter["materialization"]["unique_key"] = _as_list(charter["materialization"]["unique_key"], True)
    if isinstance(charter.get("validation"), dict) and "tests" in charter["validation"]:
        charter["validation"]["tests"] = _as_list(charter["validation"]["tests"], False)
    if "metrics" in charter:
        metrics = _as_list(charter["metrics"], False) or []
        charter["metrics"] = [m if isinstance(m, dict) else {"name": m} for m in metrics]
    return charter


def _charter(table: dict) -> dict:
    """The table's parsed charter (set by the parser), or {} when it has none."""
    charter = table.get("charter")
    return charter if isinstance(charter, dict) else {}


def _present(charter: dict, key: str) -> bool:
    value = charter.get(key)
    if value is None or value == "" or value == {}:
        return False
    return value != [] or key == "replaces"


def completeness(table: dict, layer: str | None) -> tuple[list[str], list[str]]:
    """Return (required keys for this layer, missing keys)."""
    required = list(REQUIRED_KEYS.get(layer, []))
    charter = _charter(table)
    return required, [key for key in required if not _present(charter, key)]


def _finding(rule: str, severity: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "message": message}


def _names(columns) -> str:
    return ", ".join(columns)


def _unique_keys(table: dict) -> list[set[str]]:
    """Column sets enforced unique by the PK, [unique] columns, or pk/unique indexes."""
    keys = []
    pk = {c["name"] for c in table["columns"] if c.get("pk")}
    if pk:
        keys.append(pk)
    keys += [{c["name"]} for c in table["columns"] if c.get("unique")]
    for index in table.get("indexes", []):
        match = re.match(r"^\s*(\((?P<cols>[^)]*)\)|(?P<col>[^\s\[]+))\s*(\[(?P<settings>.*)\])?\s*$", index)
        if not match:
            continue
        settings = {s.strip().lower() for s in (match["settings"] or "").split(",")}
        if settings & {"pk", "unique"}:
            cols = match["cols"] if match["cols"] is not None else match["col"]
            keys.append({c.strip().strip('"`') for c in cols.split(",") if c.strip()})
    return keys


def resolve(name: str, names: set[str]) -> str | None:
    """Match a declared model name to one of ``names``, allowing case and schema differences.

    ``analytics.dim_x`` and ``dim_x`` resolve to each other when the match is unique.
    """
    from dbml_viewer.source_compare import _match_table  # deferred: source_compare imports the parser

    return _match_table(list(dict.fromkeys([name, name.rsplit(".", 1)[-1]])), names)


def _table_rules(table: dict, layer: str | None, status: str | None, all_names: set[str],
                 known_models: set[str]) -> list[dict]:
    name = table["name"]
    columns = {c["name"] for c in table["columns"]}
    charter, error = table.get("charter"), table.get("charter_error")
    out = []
    if error:
        out.append(_finding("invalid-yaml", "error", f"{name}: {error} The note is shown as prose."))
    # Tables without a charter block (e.g. DBML exported from dbt) get no
    # completeness or style findings unless they are new in this branch.
    if charter is None and error is None and status != "new":
        return out

    _, missing = completeness(table, layer)
    if missing and not error:  # invalid-yaml already covers an unreadable block
        severity = "error" if status == "new" else "warning"
        label = LAYER_LABELS.get(layer, "This")
        out.append(_finding("required-key-missing", severity,
                            f"{label} model {name} is missing required charter keys: {_names(missing)}."))

    bare = name.rsplit(".", 1)[-1].lower()
    prefixes = NAMING_PREFIXES.get(layer)
    if prefixes and not bare.startswith(prefixes):
        out.append(_finding("naming-prefix", "info",
                            f"{name} is in the {layer} layer but does not start with {', '.join(prefixes)}."))

    if layer == "mart":
        undocumented = [c["name"] for c in table["columns"] if not (c.get("note") or "").strip()]
        if undocumented:
            shown = _names(undocumented[:5]) + (f" and {len(undocumented) - 5} more" if len(undocumented) > 5 else "")
            out.append(_finding("undocumented-columns", "info", f"{name} has columns with no description: {shown}."))

    if charter is None:
        return out

    for key in charter:
        if key not in KNOWN_KEYS:
            close = difflib.get_close_matches(key, KNOWN_KEYS, n=1)
            hint = f" Did you mean {close[0]}?" if close else ""
            out.append(_finding("unknown-key", "warning", f"Charter key {key} on {name} is not part of the convention.{hint}"))

    for column in charter.get("pii") or []:
        if column not in columns:
            out.append(_finding("pii-column-unknown", "error", f"PII column {column} is not a column of {name}."))

    for model in charter.get("replaces") or []:
        if not resolve(model, all_names) and not resolve(model, known_models):
            # Only a model this branch adds or edits must name a real replacement.
            severity = "info" if status == "unchanged" else "warning"
            out.append(_finding("replaces-unknown", severity,
                                f"{name} replaces {model}, which is not a diagram table, a model in the branch "
                                "comparison, or a project model known to lineage."))

    grain = charter.get("grain") or []
    if not grain:
        return out
    unknown = [c for c in grain if c not in columns]
    for column in unknown:
        out.append(_finding("grain-column-unknown", "error", f"Grain column {column} is not a column of {name}."))

    grain_set = set(grain)
    keys = _unique_keys(table)
    if not keys:
        if layer not in ("source", "seed"):
            out.append(_finding("no-unique-key", "warning",
                                f"{name} declares grain ({_names(grain)}) but no primary key or unique index enforces it."))
    elif not unknown and grain_set not in keys:
        enforced = "; ".join("(" + _names(sorted(k)) + ")" for k in keys)
        out.append(_finding("grain-not-unique", "warning",
                            f"Grain ({_names(grain)}) of {name} does not match its primary key or unique indexes {enforced}."))

    materialization = charter.get("materialization")
    unique_key = materialization.get("unique_key") if isinstance(materialization, dict) else None
    if unique_key and set(unique_key) != grain_set:
        out.append(_finding("unique-key-mismatch", "error",
                            f"materialization.unique_key ({_names(unique_key)}) of {name} differs from its grain ({_names(grain)})."))

    history = str(charter.get("history") or "").lower()
    if history in ("scd2", "snapshot") and not any(t in c.lower() for c in grain for t in HISTORY_TOKENS):
        out.append(_finding("history-grain", "warning",
                            f"{name} keeps {history} history but its grain ({_names(grain)}) has no time or version column."))
    return out


def findings(data: dict, layers: dict, comparison: dict | None = None,
             known_models: set[str] | None = None) -> dict[str, list[dict]]:
    """Return {table_name: [finding]} for every charter rule.

    ``known_models`` are project model names found by lineage (stub kind
    ``model``); a ``replaces`` entry naming one of them, a model in the
    comparison's ``baseline_models``, or a removed model source is not unknown.
    ``replaces-unknown`` is info on unchanged models, a warning otherwise.

    Finding shape: {"rule": str, "severity": "error"|"warning"|"info", "message": str}
    """
    compared = (comparison or {}).get("tables", {})
    all_names = {t["name"] for t in data["tables"]} | set(compared)
    known = set(known_models or ())
    # Models at the comparison baseline, and removed model sources by file stem, are known too.
    known.update((comparison or {}).get("baseline_models") or ())
    known.update(PurePosixPath(key).stem for key, entry in compared.items()
                 if entry.get("unmapped") and entry.get("status") == "removed")
    result = {
        table["name"]: _table_rules(table, layers.get(table["name"]),
                                    compared.get(table["name"], {}).get("status"), all_names, known)
        for table in data["tables"]
    }

    owners: dict[str, list[str]] = {}
    for table in data["tables"]:
        charter = _charter(table)
        for metric in charter.get("metrics") or []:
            metric_name = metric.get("name") if isinstance(metric, dict) else None
            if metric_name and table["name"] not in owners.setdefault(str(metric_name), []):
                owners[str(metric_name)].append(table["name"])
    for metric_name, models in owners.items():
        if len(models) > 1:
            for model in models:
                others = _names(m for m in models if m != model)
                result[model].append(_finding("metric-duplicate", "warning",
                                              f"Metric {metric_name} on {model} is also defined in {others}."))
    return result
