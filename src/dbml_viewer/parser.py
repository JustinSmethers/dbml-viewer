"""Parse DBML files into a normalized dict structure using pydbml."""

from pathlib import Path

from pydbml import PyDBML


def _normalize_ref_pairs(ref):
    """Yield normalized column pairs for a DBML ref.

    `from_*` always represents the foreign-key side for directional refs.
    Composite refs are flattened into one pair per column.
    """
    if len(ref.col1) != len(ref.col2):
        raise ValueError("Ref columns must have matching lengths")

    left_cols = list(ref.col1)
    right_cols = list(ref.col2)

    if ref.type == "<":
        return right_cols, left_cols, True
    if ref.type == ">":
        return left_cols, right_cols, True
    return left_cols, right_cols, False


def parse_dbml(path: Path) -> dict:
    """Parse a DBML file and return a normalized data model dict.

    Returns:
        {
            "project": { "name", "note", "database_type" },
            "tables": [{ "name", "note", "group", "columns": [...] }],
            "refs": [{ "from_table", "from_column", "to_table", "to_column", "type" }],
            "groups": { "group_name": ["table1", ...] },
            "group_colors": { "group_name": "#RRGGBB" },  # only groups with [color: ...]
            "enums": [{ "name", "values": [...] }]
        }
    """
    db = PyDBML.parse_file(str(path))

    # Build table-to-group mapping
    table_group_map = {}
    groups = {}
    group_colors = {}
    for group in db.table_groups:
        group_tables = [item.name for item in group.items]
        groups[group.name] = group_tables
        if group.color:
            group_colors[group.name] = group.color
        for table_name in group_tables:
            table_group_map[table_name] = group.name

    # Build refs list and FK column lookup set
    refs = []
    fk_columns = set()  # (table_name, column_name) pairs that are FK sources
    for ref in db.refs:
        from_cols, to_cols, marks_fk = _normalize_ref_pairs(ref)
        for from_col_obj, to_col_obj in zip(from_cols, to_cols):
            from_table = from_col_obj.table.name
            from_col = from_col_obj.name
            to_table = to_col_obj.table.name
            to_col = to_col_obj.name
            refs.append({
                "from_table": from_table,
                "from_column": from_col,
                "to_table": to_table,
                "to_column": to_col,
                "type": ref.type,
            })
            if marks_fk:
                fk_columns.add((from_table, from_col))

    # Build tables list
    tables = []
    for table in db.tables:
        columns = []
        for col in table.columns:
            columns.append({
                "name": col.name,
                "type": str(col.type),
                "pk": col.pk,
                "fk": (table.name, col.name) in fk_columns,
                "not_null": col.not_null,
                "note": str(col.note) if col.note else "",
            })
        tables.append({
            "name": table.name,
            "note": str(table.note) if table.note else "",
            "group": table_group_map.get(table.name, "_default"),
            "columns": columns,
        })

    # Build enums list
    enums = []
    for enum in db.enums:
        enums.append({
            "name": enum.name,
            "values": [item.name for item in enum.items],
        })

    # Extract project metadata
    project = {}
    if db.project:
        project = {
            "name": db.project.name or "",
            "note": str(db.project.note) if db.project.note else "",
            "database_type": db.project.items.get("database_type", ""),
        }

    return {
        "project": project,
        "tables": tables,
        "refs": refs,
        "groups": groups,
        "group_colors": group_colors,
        "enums": enums,
    }
