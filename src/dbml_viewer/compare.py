"""Semantic DBML comparison against a branch's common Git ancestor."""

import subprocess
from pathlib import Path

from dbml_viewer.parser import parse_dbml_text


def _git(cwd: Path, *args: str, strip: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git command timed out after 30 seconds") from exc
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git comparison failed")
    return result.stdout.strip() if strip else result.stdout


def comparison_revision(root: Path, revision: str | None = None) -> str:
    """Use a freshly fetched origin/main instead of a potentially stale local main."""
    if revision in (None, "main", "origin/main") and "origin" in _git(root, "remote").splitlines():
        try:
            _git(root, "fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
        except ValueError as exc:
            raise ValueError(f"Could not refresh origin/main; refusing to use a stale main baseline: {exc}") from exc
        return "origin/main"
    if revision is not None:
        return revision
    for candidate in ("main", "origin/main"):
        try:
            _git(root, "rev-parse", "--verify", "--end-of-options", candidate + "^{commit}")
            return candidate
        except ValueError:
            pass
    raise ValueError("Neither main nor origin/main exists locally; use --compare REF or configure origin")


def compare_branch(path: Path, current: dict, revision: str) -> dict:
    """Compare the on-disk input (including local edits) with merge-base HEAD/ref."""
    path = path.resolve()
    try:
        root = Path(_git(path.parent, "rev-parse", "--show-toplevel"))
        revision = comparison_revision(root, revision)
        target = _git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        base = _git(root, "merge-base", "HEAD", target)
        relative = path.relative_to(root).as_posix()
        exists = _git(root, "ls-tree", base, "--", relative)
        baseline = parse_dbml_text(_git(root, "show", base + ":" + relative)) if exists else {
            "tables": [], "refs": [], "enums": [], "group_colors": {},
        }
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot compare with {revision!r}: {exc}") from exc
    changes = compare_models(baseline, current)
    changes.update({"revision": revision, "base": base[:8], "includes_local_edits": True})
    return changes


def _refs(data: dict) -> set:
    refs = set()
    for ref in data["refs"]:
        a = (ref["from_table"], ref["from_column"])
        b = (ref["to_table"], ref["to_column"])
        kind = ">" if ref["type"] in (">", "<") else ref["type"]
        if kind in ("-", "<>"):
            a, b = sorted((a, b))
        refs.add((*a, *b, kind))
    return refs


def compare_models(before: dict, after: dict) -> dict:
    """Report column, constraint, note, group, index, enum and relationship edits."""
    old = {t["name"]: t for t in before["tables"]}
    new = {t["name"]: t for t in after["tables"]}
    entries = {}
    enums_before = {e["name"]: e["values"] for e in before.get("enums", [])}
    enums_after = {e["name"]: e["values"] for e in after.get("enums", [])}
    changed_enums = {name for name in enums_before.keys() | enums_after.keys()
                     if enums_before.get(name) != enums_after.get(name)}
    for name in sorted(old.keys() | new.keys()):
        status = "new" if name not in old else "removed" if name not in new else "unchanged"
        details = []
        if status == "new":
            details.append(f"Added model with {len(new[name]['columns'])} columns")
        elif status == "removed":
            details.append("Removed model from the diagram")
        else:
            a, b = old[name], new[name]
            ac = {c["name"]: c for c in a["columns"]}
            bc = {c["name"]: c for c in b["columns"]}
            for column in sorted(ac.keys() | bc.keys()):
                if column not in ac:
                    details.append(f"Added column: {column} ({bc[column]['type']})")
                elif column not in bc:
                    details.append(f"Removed column: {column} ({ac[column]['type']})")
                else:
                    for field, label in (("type", "type"), ("pk", "primary key"),
                                         ("not_null", "not null"), ("unique", "unique"),
                                         ("autoinc", "increment"), ("default", "default"),
                                         ("default_kind", "default kind"),
                                         ("note", "note")):
                        if ac[column].get(field) != bc[column].get(field):
                            if field in ("type", "default"):
                                details.append(f"Changed {label}: {column}: {ac[column].get(field)} → {bc[column].get(field)}")
                            else:
                                details.append(f"Changed {label}: {column}")
            for field, label in (("note", "Model note"), ("group", "Group"), ("indexes", "Indexes")):
                if a.get(field) != b.get(field):
                    details.append(f"{label} changed")
            group = b.get("group")
            if before.get("group_colors", {}).get(group) != after.get("group_colors", {}).get(group):
                details.append("Group color changed")
            for enum in sorted(changed_enums):
                if any(c["type"] == enum for c in a["columns"] + b["columns"]):
                    details.append(f"Enum changed: {enum}")
        entries[name] = {"status": status, "details": details}

    old_refs, new_refs = _refs(before), _refs(after)
    for refs, verb in ((new_refs - old_refs, "Added"), (old_refs - new_refs, "Removed")):
        for a, ac, b, bc, kind in sorted(refs):
            detail = f"{verb} relationship: {a}.{ac} {kind} {b}.{bc}"
            for name in {a, b}:
                entries[name]["details"].append(detail)
    for entry in entries.values():
        if entry["status"] == "unchanged" and entry["details"]:
            entry["status"] = "modified"
    counts = {status: sum(e["status"] == status for e in entries.values())
              for status in ("new", "modified", "removed", "unchanged")}
    return {"tables": entries, "counts": counts}
