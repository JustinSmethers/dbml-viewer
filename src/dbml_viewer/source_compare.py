"""Map branch changes in model source files onto a generated DBML diagram."""

import json
from pathlib import Path, PurePosixPath

import yaml

from dbml_viewer.compare import _git, comparison_revision


def find_repository(path: Path, project_dir: Path | None = None) -> Path:
    """Prefer the input's repository; allow generated files outside the project."""
    candidates = [project_dir] if project_dir else [path.resolve().parent, Path.cwd()]
    for candidate in candidates:
        try:
            return Path(_git(candidate, "rev-parse", "--show-toplevel"))
        except (OSError, ValueError):
            pass
    raise ValueError("No Git checkout found; use --project-dir to select the model repository")


def _files(root: Path, *args: str) -> set[str]:
    return set(filter(None, _git(root, *args, strip=False).split("\0")))


def _model_paths(root: Path, files: set[str], base: str) -> tuple[list[PurePosixPath], list[Path]]:
    """Read model-paths from dbt projects, including a deleted project's baseline."""
    directories, manifests = [], []
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
        config = yaml.safe_load(texts[0]) or {}
        manifests.append(root / parent / config.get("target-path", "target") / "manifest.json")
    if not projects:
        # Conventional models directories also work without a dbt project file.
        directories = sorted({PurePosixPath(*PurePosixPath(name).parts[:i + 1])
                              for name in files for i, part in enumerate(PurePosixPath(name).parts[:-1])
                              if part == "models"})
        manifests.append(root / "target/manifest.json")
    return directories, manifests


def _manifest_names(root: Path, manifests: list[Path]) -> dict[str, list[str]]:
    names = {}
    for path in manifests:
        if not path.is_file():
            continue
        manifest = json.loads(path.read_text())
        # target-path can be customized, so locate the project from its config.
        project = next((parent for parent in path.parents if (parent / "dbt_project.yml").is_file()), root)
        for node in manifest.get("nodes", {}).values():
            if node.get("resource_type") != "model" or not node.get("original_file_path"):
                continue
            project_name = manifest.get("metadata", {}).get("project_name")
            if project_name and node.get("package_name", project_name) != project_name:
                continue
            source = (project / node["original_file_path"]).relative_to(root).as_posix()
            alias = node.get("alias") or node["name"]
            names.setdefault(source, []).extend([
                f"{node.get('schema')}.{alias}", alias, node["name"],
            ])
    return names


def compare_sources(root: Path, current: dict, revision: str | None = None) -> dict:
    """Compare merge-base to working tree, plus non-ignored untracked models."""
    try:
        revision = comparison_revision(root, revision)
        target = _git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        base = _git(root, "merge-base", "HEAD", target)
        baseline = _files(root, "ls-tree", "-r", "--name-only", "-z", base)
        working = _files(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        directories, manifests = _model_paths(root, baseline | working, base)
        aliases = _manifest_names(root, manifests)

        def is_model(name):
            path = PurePosixPath(name)
            return path.suffix in (".sql", ".py") and any(path.is_relative_to(directory) for directory in directories)

        # Disable rename inference: file renames are removals/additions, and moves
        # preserving a model name are combined below into one modified model.
        tokens = _git(root, "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, "--", strip=False).split("\0")
        changed = {name: status for status, name in zip(tokens[::2], tokens[1::2]) if name and is_model(name)}
        for name in working - baseline:
            if is_model(name) and (root / name).is_file():
                changed[name] = "A"

        table_names = {table["name"] for table in current["tables"]}

        def match(name):
            candidates = aliases.get(name, []) + [PurePosixPath(name).stem]
            for candidate in candidates:
                if candidate in table_names:
                    return candidate
            matches = {table for table in table_names if table.rsplit(".", 1)[-1] in candidates}
            return next(iter(matches)) if len(matches) == 1 else None

        # Do not mark a card when several live sources could own it.
        owners = {}
        for name in working:
            if is_model(name) and (root / name).is_file():
                table = match(name)
                if table:
                    owners.setdefault(table, set()).add(name)

        entries = {name: {"status": "unchanged", "details": []} for name in sorted(table_names)}
        mapped_changes = {}
        unmapped = []
        for name, status in sorted(changed.items()):
            table = match(name)
            if not table or len(owners.get(table, set())) > 1:
                unmapped.append(name)
                table = name  # Still reviewable even when absent/ambiguous in DBML.
            mapped_changes.setdefault(table, []).append((name, status))
        for table, changes in mapped_changes.items():
            statuses = {status for _, status in changes}
            status = "new" if statuses == {"A"} else "removed" if statuses == {"D"} else "modified"
            details = [{"A": "Added", "D": "Removed"}.get(kind, "Modified") + f" model source: {name}"
                       for name, kind in changes]
            if table in unmapped:
                details.append("No unique diagram card matched this model source")
            entries[table] = {"status": status, "details": details}
        counts = {status: sum(entry["status"] == status for entry in entries.values())
                  for status in ("new", "modified", "removed", "unchanged")}
        return {"tables": entries, "counts": counts, "revision": revision, "base": base[:8],
                "includes_local_edits": True, "mode": "sources", "unmapped": unmapped}
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot compare model sources with {revision or 'main'!r}: {exc}") from exc
