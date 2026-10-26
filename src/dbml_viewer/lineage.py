"""Model lineage from dbt SQL, a local manifest, and charter ``depends_on``.

Lineage is data flow between models. It is kept separate from DBML refs,
which describe joins. Edge sources, in order of preference:

1. ``ref()`` / ``source()`` calls parsed from model SQL and Python files
2. ``depends_on.nodes`` in a local dbt manifest, when one is present
3. ``depends_on`` declared in a model charter (proposals without SQL)

Nothing is compiled or executed; only literal calls are recognised.
"""

import heapq
import re
from pathlib import Path

from dbml_viewer.charter import _charter
from dbml_viewer.source_compare import _identifiers, _inline_names, _match_table, _read_manifest

PREFERENCE = ("sql", "manifest", "declared")
# Lineage order for violations and tie-breaking. Bridges follow the marts they join.
ORDER = ("source", "seed", "prep", "intermediate", "mart", "bridge")
SQL_NOTE = "SQL lineage parses literal ref('x') / ref('pkg','x') / source('s','t'); dynamic refs need a manifest."
GRAIN_MESSAGE = "aggregation required: confirm rollup before join"

_STRING = r"""(?:'[^'\\\n]*'|"[^"\\\n]*")"""
_CALL = re.compile(
    r"(?<![\w.])(?:dbt\s*\.\s*)?(ref|source)\s*\(\s*(" + _STRING + r"(?:\s*,\s*" + _STRING + r")*)"
    r"\s*(?:,\s*\w+\s*=\s*[^()]*)?\)"
)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_STUB_RANK = {"source": 0, "model": 1, "unknown": 2}


def parse_refs(sql_text: str) -> list[tuple[str, str]]:
    """Return literal ``("ref", name)`` / ``("source", "src.tbl")`` calls in order, de-duplicated.

    Handles Jinja (``{{ ref( 'x' ) }}``, ``ref('pkg', 'x')``, ``ref('x', v=2)``) and
    Python models (``dbt.ref("x")``, ``dbt.source("s", "t")``). Jinja comments are ignored.
    """
    found = []
    for kind, arguments in _CALL.findall(_JINJA_COMMENT.sub("", sql_text or "")):
        values = [value[1:-1] for value in re.findall(_STRING, arguments)]
        if kind == "ref" and len(values) in (1, 2):
            found.append(("ref", values[-1]))
        elif kind == "source" and len(values) == 2:
            found.append(("source", ".".join(values)))
    return list(dict.fromkeys(found))


def _rank(layer: str | None) -> int:
    # Unclassified tables are placed as intermediate, matching the layout.
    return ORDER.index(layer) if layer in ORDER else ORDER.index("intermediate")


def _names(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def build(data: dict, layers: dict, *, root: Path | str | None = None,
          model_dirs: list[str] | None = None, manifests: list[str] | None = None) -> dict:
    """Return the lineage dict: edges, stubs, order, notes, per-table closures."""
    tables = [table["name"] for table in data["tables"]]
    table_names = set(tables)
    notes = [SQL_NOTE]
    edges: dict[tuple[str, str], set[str]] = {}
    stub_kinds: dict[str, str] = {}
    project: dict[str, list[str]] = {}  # model name (case-folded) -> identifiers
    known: set[str] = set()  # case-folded project model names and aliases

    def index(name: str, identifiers: list[str]):
        project.setdefault(name.casefold(), []).extend(identifiers)
        known.update(item.casefold() for item in identifiers + [name])

    def resolve_model(name: str) -> str:
        # A declared ``analytics.dim_x`` also matches the diagram table or project model ``dim_x``.
        bare = name.rsplit(".", 1)[-1]
        candidates = project.get(name.casefold(), []) + project.get(bare.casefold(), []) + [name, bare]
        return _match_table(list(dict.fromkeys(candidates)), table_names) or name

    def is_known(name: str) -> bool:
        return name.casefold() in known or name.rsplit(".", 1)[-1].casefold() in known

    def resolve_source(source: str, name: str, identifier: str | None = None, schema: str | None = None) -> str:
        qualified = f"{source}.{name}"
        table = _match_table([qualified] + _identifiers(name, identifier, schema), table_names)
        # Unqualified matches only count for input tables, never for a same-named mart.
        if table and (table.casefold() == qualified.casefold() or layers.get(table) in ("source", "seed", None)):
            return table
        return qualified

    def add(upstream: str, downstream: str, source: str, kind: str):
        if upstream == downstream or downstream not in table_names:
            return
        if upstream not in table_names:
            if kind != "unknown":
                known.add(upstream.casefold())  # e.g. a package model reached by ref()
            current = stub_kinds.get(upstream)
            if current is None or _STUB_RANK[kind] < _STUB_RANK[current]:
                stub_kinds[upstream] = kind
        edges.setdefault((upstream, downstream), set()).add(source)

    if root is None:
        notes.append("No model repository: lineage uses charter depends_on only.")
    else:
        root = Path(root)
        _scan_sql(root, model_dirs if model_dirs is not None else ["models"], table_names,
                  index, resolve_model, resolve_source, add, notes)
        for relative in (manifests if manifests is not None else ["target/manifest.json"]):
            _read_lineage_manifest(root, relative, table_names, index, resolve_source, add, notes)

    for table in data["tables"]:
        for name in _names(_charter(table).get("depends_on")):
            upstream = resolve_model(name)
            add(upstream, table["name"], "declared", "model" if is_known(name) else "unknown")

    order = _topological(tables, layers, edges, notes)
    position = {name: index for index, name in enumerate(order)}

    def key(name):
        return (1, position[name], "") if name in position else (0, 0, name)

    upstream_of = {name: set() for name in tables}
    downstream_of = {name: set() for name in tables}
    for upstream, downstream in edges:
        upstream_of[downstream].add(upstream)
        if upstream in downstream_of:
            downstream_of[upstream].add(downstream)

    charters = {table["name"]: _charter(table) for table in data["tables"]}
    result_tables = {}
    for name in order:
        impact = closure(name, downstream_of)
        consumers = []
        for member in [name] + impact:
            consumers.extend(_names(charters[member].get("consumers")))
        result_tables[name] = {
            "upstream": sorted(upstream_of[name], key=key),
            "downstream": sorted(downstream_of[name], key=key),
            "impact": sorted(impact, key=key),
            "consumers_affected": list(dict.fromkeys(consumers)),
        }

    edge_list = []
    for (upstream, downstream), sources in sorted(edges.items(), key=lambda item: (key(item[0][0]), key(item[0][1]))):
        ranked = [source for source in PREFERENCE if source in sources]
        edge = {"from": upstream, "to": downstream, "source": ranked[0]}
        if len(ranked) > 1:
            edge["sources"] = ranked
        edge_list.append(edge)
    return {
        "edges": edge_list,
        "stubs": sorted(stub_kinds),
        "stub_kinds": dict(sorted(stub_kinds.items())),
        "order": order,
        "notes": notes,
        "tables": result_tables,
    }


def _scan_sql(root, model_dirs, table_names, index, resolve_model, resolve_source, add, notes):
    files = {}
    for directory in model_dirs:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix in (".sql", ".py") and path.is_file():
                files.setdefault(path.resolve(), path)
    parsed = []
    for path in files.values():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        identifiers = _inline_names(text, path.stem) + [path.stem]
        index(path.stem, identifiers)
        parsed.append((identifiers, text))
    # Resolve after indexing every file so refs to aliased models map correctly.
    matched = 0
    for identifiers, text in parsed:
        table = _match_table(identifiers, table_names)
        if not table:
            continue
        matched += 1
        for kind, name in parse_refs(text):
            if kind == "ref":
                add(resolve_model(name), table, "sql", "model")
            else:
                add(resolve_source(*name.split(".", 1)), table, "sql", "source")
    where = ", ".join(model_dirs)
    if parsed:
        notes.append(f"Scanned {len(parsed)} model files in {where}; {matched} matched diagram tables.")
    else:
        notes.append(f"No model SQL found in {where}." if where else "No model directories found; lineage uses the manifest and charter depends_on.")


def _read_lineage_manifest(root, relative, table_names, index, resolve_source, add, notes):
    path = Path(root) / relative
    if not path.is_file():
        notes.append(f"No dbt manifest at {relative}; macro-generated and dynamic refs are not included.")
        return
    try:
        manifest = _read_manifest(path)
    except (OSError, ValueError) as exc:
        notes.append(f"Ignored unreadable manifest {relative}: {exc}")
        return
    nodes = {key: node for key, node in (manifest.get("nodes") or {}).items() if isinstance(node, dict) and node.get("name")}
    sources = {key: node for key, node in (manifest.get("sources") or {}).items() if isinstance(node, dict) and node.get("name")}

    def identifiers(node):
        return _identifiers(node["name"], node.get("alias"), node.get("schema"))

    models = {key: node for key, node in nodes.items() if node.get("resource_type") == "model"}
    for node in nodes.values():
        if node.get("resource_type") in ("model", "seed", "snapshot"):
            index(node["name"], identifiers(node))
    for node in models.values():
        table = _match_table(identifiers(node), table_names)
        if not table:
            continue
        depends = (node.get("depends_on") or {}).get("nodes") or []
        for dependency in depends if isinstance(depends, list) else []:
            if dependency in sources:
                source = sources[dependency]
                add(resolve_source(source.get("source_name") or source.get("schema") or "", source["name"],
                                   source.get("identifier"), source.get("schema")), table, "manifest", "source")
            elif dependency in nodes:
                upstream = nodes[dependency]
                if upstream.get("resource_type") in ("model", "seed", "snapshot"):
                    add(_match_table(identifiers(upstream), table_names) or upstream["name"], table, "manifest", "model")
            elif isinstance(dependency, str) and dependency.startswith(("model.", "seed.", "snapshot.")):
                add(dependency.split(".")[-1], table, "manifest", "model")
    notes.append(f"Manifest used: {relative} ({len(models)} models).")


def _topological(tables, layers, edges, notes) -> list[str]:
    """Kahn's algorithm over diagram tables; ties go to the earlier layer, then the name."""
    names = set(tables)
    children = {name: set() for name in tables}
    indegree = {name: 0 for name in tables}
    for upstream, downstream in edges:
        if upstream in names and downstream not in children[upstream]:
            children[upstream].add(downstream)
            indegree[downstream] += 1

    def key(name):
        return (_rank(layers.get(name)), name)

    heap = [key(name) for name in tables if indegree[name] == 0]
    heapq.heapify(heap)
    queued = {name for _, name in heap}
    order, cyclic = [], []
    while len(order) < len(tables):
        if not heap:
            # A cycle: release its earliest member and keep going deterministically.
            name = min((name for name in tables if name not in queued), key=key)
            cyclic.append(name)
            heapq.heappush(heap, key(name))
            queued.add(name)
        _, name = heapq.heappop(heap)
        order.append(name)
        for child in sorted(children[name]):
            indegree[child] -= 1
            if indegree[child] <= 0 and child not in queued:
                heapq.heappush(heap, key(child))
                queued.add(child)
    if cyclic:
        notes.append("Lineage contains a cycle through " + ", ".join(cyclic) + "; order is approximate there.")
    return order


def closure(name: str, downstream_of: dict[str, set[str]]) -> list[str]:
    """Every model reachable downstream of ``name``, excluding itself, sorted by name."""
    seen, stack = set(), [name]
    while stack:
        for child in downstream_of.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    seen.discard(name)
    return sorted(seen)


def findings(data: dict, layers: dict, lineage: dict, statuses: dict | None = None) -> dict[str, list[dict]]:
    """Return {table: [finding]} for layer, declared-upstream, grain, and unknown-upstream rules.

    ``statuses`` maps table names to branch comparison statuses; a ``new``
    model with no upstream at all gets a ``no-upstream`` hint.
    """
    result = {table["name"]: [] for table in data["tables"]}
    charters = {table["name"]: _charter(table) for table in data["tables"]}
    columns = {table["name"]: {column["name"].casefold() for column in table.get("columns", [])}
               for table in data["tables"]}
    kinds = lineage.get("stub_kinds", {})
    incoming: dict[str, list[dict]] = {}
    for edge in lineage.get("edges", []):
        if edge["to"] in result:
            incoming.setdefault(edge["to"], []).append(edge)

    def finding(rule, severity, message, inferred=False):
        item = {"rule": rule, "severity": severity, "message": message}
        if inferred:
            item["inferred"] = True
        return item

    for name, edges in incoming.items():
        layer = layers.get(name)
        for edge in edges:
            upstream = edge["from"]
            sources = edge.get("sources", [edge["source"]])
            # Declared and manifest edges are facts; SQL-only edges are inferred.
            inferred = set(sources) == {"sql"}
            upstream_layer = layers.get(upstream) if upstream in result else (
                "source" if kinds.get(upstream) == "source" else None)
            if layer in ORDER and upstream_layer in ORDER:
                message = None
                if layer in ("mart", "bridge") and upstream_layer in ("source", "seed"):
                    message = f"{layer.capitalize()} reads {upstream_layer} `{upstream}` directly; route it through a prep model."
                elif ORDER.index(layer) < ORDER.index(upstream_layer):
                    message = f"Backward layer edge: this {layer} model reads {upstream_layer} `{upstream}`."
                if message:
                    result[name].append(finding("layer-violation", "warning", message, inferred))
            grain = _names(charters[name].get("grain"))
            upstream_grain = _names(charters.get(upstream, {}).get("grain"))
            if grain and upstream_grain:
                mine = {column.casefold() for column in grain}
                theirs = {column.casefold() for column in upstream_grain}
                if mine < theirs:
                    result[name].append(finding(
                        "grain-compatibility", "warning",
                        f"{GRAIN_MESSAGE}. Upstream `{upstream}` grain ({', '.join(upstream_grain)}) "
                        f"is finer than this model's grain ({', '.join(grain)}).", inferred))
                elif not theirs <= columns[name]:
                    # e.g. an account x month mart fed by a per-subscription model. When every
                    # upstream grain column is a column here, it is a key lookup join instead.
                    result[name].append(finding(
                        "grain-compatibility", "warning",
                        f"Upstream `{upstream}` grain ({', '.join(upstream_grain)}) is not carried by "
                        "this model's columns: confirm the rollup or join.", inferred))
            if "declared" in sources and upstream not in result and kinds.get(upstream) == "unknown":
                result[name].append(finding(
                    "upstream-unknown", "info",
                    f"Declared upstream `{upstream}` is not a diagram table or a known project model."))

        if "depends_on" not in charters[name]:
            continue
        observed = [edge for edge in edges if {"sql", "manifest"} & set(edge.get("sources", [edge["source"]]))]
        if not observed:
            continue
        kinds_seen = {source for edge in observed for source in edge.get("sources", [edge["source"]])} & {"sql", "manifest"}
        where = "SQL or the manifest" if len(kinds_seen) == 2 else "SQL" if "sql" in kinds_seen else "the manifest"
        observed_names = {edge["from"] for edge in observed}
        for edge in edges:
            sources = edge.get("sources", [edge["source"]])
            if "declared" in sources and edge["from"] not in observed_names:
                result[name].append(finding(
                    "declared-upstream-mismatch", "warning",
                    f"Declared upstream `{edge['from']}` is not referenced in {where}."))
            elif "declared" not in sources and edge["from"] in observed_names:
                result[name].append(finding(
                    "declared-upstream-mismatch", "info",
                    f"`{edge['from']}` is referenced in {where} but not declared in depends_on.", True))

    for name in result:
        # Sources and seeds are inputs; they have no upstream models by design.
        if (statuses or {}).get(name) == "new" and name not in incoming and layers.get(name) not in ("source", "seed"):
            result[name].append(finding(
                "no-upstream", "info",
                f"No upstream known for {name}. Add depends_on to its charter to place it in lineage."))
    return result
