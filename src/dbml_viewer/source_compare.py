"""Map branch changes in model source files onto a generated DBML diagram."""

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Callable

import yaml

from dbml_viewer.compare import _git, compare_models, comparison_revision
from dbml_viewer.parser import parse_dbml, parse_dbml_text


def find_repository(path: Path, project_dir: Path | None = None) -> Path:
    """Prefer the input's repository; allow generated files outside the project."""
    candidates = [project_dir] if project_dir else [path.resolve().parent, Path.cwd()]
    for candidate in candidates:
        try:
            return Path(_git(candidate, "rev-parse", "--show-toplevel"))
        except (OSError, ValueError):
            pass
    raise ValueError("No Git checkout found; use --project-dir to select the model repository")


def find_project(path: Path, project_dir: Path | None = None) -> Path | None:
    """Nearest directory containing dbt_project.yml, for SQL lineage without Git.

    Walks up from ``project_dir``, then the DBML file's directory, then the
    current directory; returns None when none of them is inside a dbt project.
    """
    starts = ([project_dir] if project_dir else []) + [path.resolve().parent, Path.cwd()]
    for start in starts:
        start = Path(start).resolve()
        for candidate in (start, *start.parents):
            if (candidate / "dbt_project.yml").is_file():
                return candidate
    return None


def _files(root: Path, *args: str) -> set[str]:
    return set(filter(None, _git(root, *args, strip=False).split("\0")))


def _model_paths(root: Path, files: set[str], base: str) -> tuple[list[PurePosixPath], list[Path], list[PurePosixPath], list[PurePosixPath]]:
    """Read model-paths from dbt projects, including a deleted project's baseline."""
    directories, manifests, upstream, seeds = [], [], [], []
    projects = sorted(name for name in files if PurePosixPath(name).name == "dbt_project.yml")
    for name in projects:
        config_path = root / name
        texts = [config_path.read_text()] if config_path.is_file() else []
        if name in _files(root, "ls-tree", "--name-only", "-z", base, "--", name):
            texts.append(_git(root, "show", f"{base}:{name}"))
        if not texts:
            continue
        parent = PurePosixPath(name).parent
        for text in texts:
            config = yaml.safe_load(text) or {}
            directories.extend(parent / directory for directory in config.get("model-paths", ["models"]))
            seeds.extend(parent / directory for directory in config.get("seed-paths", ["seeds"]))
            upstream.extend(parent / directory for key, default in (("seed-paths", "seeds"), ("snapshot-paths", "snapshots"))
                            for directory in config.get(key, [default]))
        config = yaml.safe_load(texts[0]) or {}
        manifests.append(root / parent / config.get("target-path", "target") / "manifest.json")
    if not projects:
        # Conventional models directories also work without a dbt project file.
        directories = sorted({PurePosixPath(*PurePosixPath(name).parts[:i + 1])
                              for name in files for i, part in enumerate(PurePosixPath(name).parts[:-1])
                              if part == "models"})
        manifests.append(root / "target/manifest.json")
        upstream = sorted({PurePosixPath(*PurePosixPath(name).parts[:i + 1])
                           for name in files for i, part in enumerate(PurePosixPath(name).parts[:-1])
                           if part in ("seeds", "snapshots")})
        seeds = [directory for directory in upstream if directory.name == "seeds"]
    return directories, manifests, upstream, seeds


def project_paths(root: Path) -> tuple[list[str], list[str]]:
    """Model directories and manifest paths from working-tree dbt_project.yml files.

    Used for lineage when no branch comparison ran. Needs no Git history (or
    no Git at all); defaults to ``models`` and ``target/manifest.json``.
    """
    try:
        files = _files(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    except (OSError, ValueError):
        # Not a Git checkout (see find_project): read the root's own dbt_project.yml.
        files = {"dbt_project.yml"} if (root / "dbt_project.yml").is_file() else set()
    directories, manifests = [], []
    for name in sorted(name for name in files if PurePosixPath(name).name == "dbt_project.yml"):
        try:
            config = yaml.safe_load((root / name).read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(config, dict):
            continue
        parent = PurePosixPath(name).parent
        directories.extend((parent / directory).as_posix() for directory in config.get("model-paths", ["models"]))
        manifests.append((parent / config.get("target-path", "target") / "manifest.json").as_posix())
    return (list(dict.fromkeys(directories)) or ["models"]), (list(dict.fromkeys(manifests)) or ["target/manifest.json"])


DIFF_LINES = 300


def source_diff(root: Path, base: str, name: str, untracked: bool = False) -> str | None:
    """Unified diff of one source file against ``base`` (None for binary or empty diffs).

    Untracked files are diffed against /dev/null, where exit code 1 means
    "differences found". Long diffs keep the first DIFF_LINES lines.
    """
    args = (["diff", "--no-ext-diff", "--no-color", "--no-index", "--", "/dev/null", name] if untracked
            else ["diff", "--no-ext-diff", "--no-color", base, "--", name])
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode not in ((0, 1) if untracked else (0,)) or not result.stdout.strip():
        return None
    lines = result.stdout.splitlines()
    if any(line.startswith(("Binary files ", "GIT binary patch")) for line in lines):
        return None
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [f"… ({len(lines) - DIFF_LINES} more lines)"]
    return "\n".join(lines)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _identifiers(name: str, alias: str | None = None, schema: str | None = None) -> list[str]:
    names = [value for value in (alias, name) if value]
    return ([f"{schema}.{alias or name}"] if schema else []) + names


def _read_manifest(path: Path) -> dict:
    """Load a dbt manifest, raising ValueError when its structure is unusable."""
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict) or not all(isinstance(manifest.get(key, {}), dict) for key in ("nodes", "sources", "metadata")):
        raise ValueError("invalid manifest structure")
    return manifest


def _match_table(candidates: list[str], table_names: set[str]) -> str | None:
    """Map model identifiers to one diagram table: exact, then case-folded, then unqualified."""
    for candidate in candidates:
        if candidate in table_names:
            return candidate
    folded = {candidate.casefold() for candidate in candidates}
    matches = {table for table in table_names if table.casefold() in folded}
    if not matches:
        matches = {table for table in table_names if table.rsplit(".", 1)[-1].casefold() in folded}
    return next(iter(matches)) if len(matches) == 1 else None


def _manifest_names(root: Path, manifests: list[Path], warnings: list[str]) -> tuple[dict[str, list[str]], set[str]]:
    names, external = {}, set()
    for path in manifests:
        if not path.is_file():
            continue
        try:
            manifest = _read_manifest(path)
        except (OSError, ValueError) as exc:
            warnings.append(f"Cannot read {path.relative_to(root)}; falling back to project files: {exc}")
            continue
        # target-path can be customized, so locate the project from its config.
        project = next((parent for parent in path.parents if (parent / "dbt_project.yml").is_file()), root)
        for node in list(manifest.get("nodes", {}).values()) + list(manifest.get("sources", {}).values()):
            if not isinstance(node, dict) or not node.get("name"):
                continue
            project_name = manifest.get("metadata", {}).get("project_name")
            if project_name and node.get("package_name", project_name) != project_name:
                continue
            if node.get("resource_type") in ("source", "seed", "snapshot"):
                external.update(_identifiers(node["name"], node.get("identifier") or node.get("alias"), node.get("schema")))
            if node.get("resource_type") not in ("model", "seed") or not node.get("original_file_path"):
                continue
            source = (project / node["original_file_path"]).relative_to(root).as_posix()
            alias = node.get("alias") or node["name"]
            names.setdefault(source, []).extend([
                f"{node.get('schema')}.{alias}", alias, node["name"],
            ])
    return names, external


def _project_names(root: Path, files: set[str], base: str, warnings: list[str],
                   changed_baseline: set[str]) -> tuple[set[str], dict[str, list[str]]]:
    """Read declarations without compiling dbt or querying a database."""
    names, aliases = set(), {}
    for name in sorted(files):
        path = PurePosixPath(name)
        if path.suffix not in (".yml", ".yaml") or path.name == "dbt_project.yml":
            continue
        source = root / name
        try:
            texts = [source.read_text()] if source.is_file() else [_git(root, "show", f"{base}:{name}")]
            if source.is_file() and name in changed_baseline:
                texts.append(_git(root, "show", f"{base}:{name}"))
        except (OSError, ValueError, yaml.YAMLError):
            warnings.append(f"Cannot read declarations in {name}; falling back to file names")
            continue
        for text in texts:
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                warnings.append(f"Cannot read declarations in {name}; falling back to file names")
                continue
            if not isinstance(data, dict):
                continue
            for kind in ("models", "seeds", "snapshots"):
                for item in data.get(kind, []) or []:
                    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                        continue
                    config = item.get("config") or {}
                    if not isinstance(config, dict):
                        config = {}
                    identifiers = _identifiers(item["name"], config.get("alias"), config.get("schema"))
                    names.update(identifiers)
                    if kind in ("models", "seeds"):
                        aliases.setdefault(item["name"], []).extend(identifiers)
            for item in data.get("sources", []) or []:
                if not isinstance(item, dict):
                    continue
                for table in item.get("tables", []) or []:
                    if isinstance(table, dict) and isinstance(table.get("name"), str):
                        names.update(_identifiers(table["name"], table.get("identifier"), item.get("schema") or item.get("name")))
    return names, aliases


def _inline_names(text: str, stem: str) -> list[str]:
    """Recognize literal Jinja config names; never execute project code."""
    config = re.search(r"\{\{\s*config\s*\((.*?)\)\s*\}\}", text, re.DOTALL)
    if not config:
        return []
    values = {match[0]: match[2] for match in re.findall(r"\b(alias|schema)\s*=\s*(['\"])(.*?)\2", config[1])}
    return _identifiers(stem, values.get("alias"), values.get("schema"))


def _external_table(table: dict) -> bool:
    """Exempt external inputs, while allowing proposed seeds in any layer group."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", table["name"] + " " + table.get("group", ""))
    tokens = set(re.split(r"[^a-z0-9]+", text.lower()))
    if tokens & {"seed", "seeds", "reference"}:
        return False
    return bool(tokens & {"source", "sources", "src", "raw", "snapshot", "snapshots", "external", "landing"})


def _design_baseline(root: Path, base: str, files: set[str], dbml_path: Path | None,
                     baseline_dbml: Path | None) -> tuple[dict | None, str | None]:
    if baseline_dbml is not None:
        return parse_dbml(baseline_dbml), str(baseline_dbml)
    if dbml_path is not None:
        try:
            relative = dbml_path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None, None
        if relative in files:
            return parse_dbml_text(_git(root, "show", f"{base}:{relative}")), f"{base[:8]}:{relative}"
    return None, None


def compare_sources(root: Path, current: dict, revision: str | None = None, *,
                    dbml_path: Path | None = None, baseline_dbml: Path | None = None,
                    progress: Callable[[str], None] | None = None) -> dict:
    """Combine branch source changes, project name inventory, and design changes."""
    report = progress or (lambda message: None)
    try:
        revision = comparison_revision(root, revision, progress=report)
        report(f"Finding the common ancestor of HEAD and {revision}")
        target = _git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        base = _git(root, "merge-base", "HEAD", target)
        report("Scanning tracked files, local edits, and untracked files")
        baseline = _files(root, "ls-tree", "-r", "--name-only", "-z", base)
        working = _files(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        tokens = _git(root, "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, "--", strip=False).split("\0")
        file_changes = {name: status for status, name in zip(tokens[::2], tokens[1::2]) if name}
        report("Reading dbt configuration, manifests, and YAML declarations")
        directories, manifests, upstream, seeds = _model_paths(root, baseline | working, base)
        warnings = []
        aliases, external_names = _manifest_names(root, manifests, warnings)
        declared_names, declared_aliases = _project_names(root, baseline | working, base, warnings, baseline & file_changes.keys())
        known_names = external_names | declared_names
        known_names.update(PurePosixPath(name).stem for name in baseline | working
                           if PurePosixPath(name).suffix in (".csv", ".sql", ".py")
                           and any(PurePosixPath(name).is_relative_to(directory) for directory in upstream))

        def is_model(name):
            path = PurePosixPath(name)
            return path.suffix in (".sql", ".py") and any(path.is_relative_to(directory) for directory in directories)

        def is_seed(name):
            path = PurePosixPath(name)
            return path.suffix == ".csv" and any(path.is_relative_to(directory) for directory in seeds)

        def is_resource(name):
            return is_model(name) or is_seed(name)

        # Inventory unchanged models as well as changed ones. A missing build or
        # manifest must not hide new design models or existing literal aliases.
        report("Reading model and seed names and aliases")
        for name in sorted(baseline | working):
            if not is_resource(name):
                continue
            stem = PurePosixPath(name).stem
            source = root / name
            text = ""
            if is_model(name):
                text = source.read_text() if source.is_file() else _git(root, "show", f"{base}:{name}") if name in baseline else ""
            aliases[name] = _inline_names(text, stem) + declared_aliases.get(stem, []) + aliases.get(name, []) + [stem]
            if is_model(name) and source.is_file() and name in baseline and name in file_changes:
                aliases[name].extend(_inline_names(_git(root, "show", f"{base}:{name}"), stem))
            known_names.update(aliases[name])

        # Disable rename inference: file renames are removals/additions, and moves
        # preserving a model name are combined below into one modified model.
        changed = {name: status for name, status in file_changes.items() if is_resource(name)}
        for name in working - baseline:
            if is_resource(name) and (root / name).is_file():
                changed[name] = "A"

        report(f"Matching source changes to {len(current['tables'])} diagram tables")
        table_names = {table["name"] for table in current["tables"]}

        def match(name):
            return _match_table(aliases.get(name, []) + [PurePosixPath(name).stem], table_names)

        # Do not mark a card when several live sources could own it.
        owners = {}
        for name in working:
            if is_resource(name) and (root / name).is_file():
                table = match(name)
                if table:
                    owners.setdefault(table, set()).add(name)

        report("Reading and parsing the earlier DBML design" + (f" ({baseline_dbml})" if baseline_dbml else " (if available)"))
        design_before, design_label = _design_baseline(root, base, baseline, dbml_path, baseline_dbml)
        # Models whose DBML card was removed: a deleted source belongs to that removed entry.
        removed_tables = ({table["name"] for table in design_before["tables"]} - table_names
                          if design_before is not None else set())

        entries = {name: {"status": "unchanged", "details": []} for name in sorted(table_names)}
        mapped_changes = {}
        unmapped = []
        for name, status in sorted(changed.items()):
            table = match(name)
            if not table and status == "D":
                table = _match_table(aliases.get(name, []) + [PurePosixPath(name).stem], removed_tables)
            elif table and len(owners.get(table, set())) > 1:
                table = None
            if not table:
                unmapped.append(name)
                table = name  # Still reviewable even when absent/ambiguous in DBML.
            mapped_changes.setdefault(table, []).append((name, status))
        for table, changes in mapped_changes.items():
            statuses = {status for _, status in changes}
            status = "new" if statuses == {"A"} else "removed" if statuses == {"D"} else "modified"
            details = [{"A": "Added", "D": "Removed"}.get(kind, "Modified") + f" model source: {name}"
                       for name, kind in changes]
            entry = {"status": status, "details": details}
            if table in unmapped:
                details.append("No unique diagram card matched this model source")
                entry["unmapped"] = True  # path-keyed: listed separately, never a walkthrough step
            diffs = []
            for name, _ in changes:
                text = source_diff(root, base, name, untracked=name not in file_changes and name not in baseline)
                if text is not None:
                    diffs.append({"path": name, "diff": text})
            if diffs:
                entry["diffs"] = diffs
            entries[table] = entry

        report("Combining model source and DBML design changes")
        if design_before is not None:
            for table, design_change in compare_models(design_before, current)["tables"].items():
                entry = entries.setdefault(table, {"status": "unchanged", "details": []})
                if design_change["status"] == "unchanged":
                    continue
                if entry["status"] == "unchanged":
                    entry["status"] = design_change["status"]
                entry["details"].extend("DBML design: " + detail for detail in design_change["details"])
                if "baseline" in design_change:
                    entry.setdefault("baseline", design_change["baseline"])

        # Only infer proposed models when an actual model project was found.
        # Ambiguous names remain known, so name matching never turns them new.
        has_project = bool(directories or seeds) or any(PurePosixPath(name).name == "dbt_project.yml" for name in baseline | working)
        if design_before is None and has_project:
            known_full = {item.casefold() for item in known_names}
            known_short = {item.rsplit(".", 1)[-1] for item in known_full}
            for table in current["tables"]:
                name = table["name"]
                if entries[name]["status"] != "unchanged" or _external_table(table):
                    continue
                if name.casefold() in known_full or name.rsplit(".", 1)[-1].casefold() in known_short:
                    continue
                entries[name] = {"status": "new", "details": [
                    "Proposed model: no matching model file, alias, or declaration found in the project or branch baseline",
                    "New status inferred from project names; no implementation or database build is required",
                ]}
        counts = {status: sum(entry["status"] == status for entry in entries.values())
                  for status in ("new", "modified", "removed", "unchanged")}
        return {"tables": entries, "counts": counts, "revision": revision, "base": base[:8],
                # The working tree (committed, staged, unstaged, untracked) vs the merge base.
                # includes_local_edits is the old name, kept for one release.
                "working_tree": True, "includes_local_edits": True, "unmapped": unmapped,
                # Model/seed names and aliases at the merge base, so a charter may replace any of them.
                "baseline_models": sorted({identifier.rsplit(".", 1)[-1] for name in baseline if is_resource(name)
                                           for identifier in aliases.get(name, [])}),
                # Without an earlier DBML design only model sources were compared.
                "mode": "hybrid" if design_before is not None else "sources",
                "design_baseline": design_label, "design_baseline_missing": design_before is None and has_project,
                "warnings": warnings,
                # Reused by lineage so review findings need not rescan the project.
                "model_dirs": [directory.as_posix() for directory in dict.fromkeys(directories)],
                "manifests": [_relative(root, path) for path in dict.fromkeys(manifests)]}
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot compare model sources with {revision or 'main'!r}: {exc}") from exc
