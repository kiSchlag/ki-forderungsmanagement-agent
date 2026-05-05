"""Dataverse Metadata Exporter.

Exports all table and column metadata from a Microsoft Dataverse environment
to a structured JSON file using the Dataverse Web API (no SDK, no scraping).
Resumable: a partial checkpoint file lets you stop and restart without
re-fetching the tables that already completed.

Output is written to ``paths.metadata_file`` (default ``<repo>/output/dataverse_metadata.json``).

Setup
-----
1. Install dependencies: ``pip install -e .``
2. Register an Entra ID confidential-client app and grant it Dataverse
   metadata read access (System Customizer or a custom role with
   ``prvReadEntity`` / ``prvReadAttribute`` is sufficient).
3. Populate ``.env`` (or environment variables) per ``.env.example``.
4. Run::

       python -m ki_forderungsmanagement.export_metadata
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import load_config
from .http_client import DataverseClient


# ---------------------------------------------------------------------------
# Dataverse metadata helpers
# ---------------------------------------------------------------------------

def _label(label_obj: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract the user-localized label text from a Dataverse Label structure."""
    if not label_obj:
        return None
    user = label_obj.get("UserLocalizedLabel")
    if user and user.get("Label"):
        return user["Label"]
    for entry in label_obj.get("LocalizedLabels") or []:
        if entry.get("Label"):
            return entry["Label"]
    return None


def _required_level(rl: Optional[Dict[str, Any]]) -> Optional[str]:
    return rl.get("Value") if rl else None


def _format_choices(option_set: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not option_set:
        return []
    return [
        {
            "value": opt.get("Value"),
            "label": _label(opt.get("Label")),
            "description": _label(opt.get("Description")),
        }
        for opt in (option_set.get("Options") or [])
    ]


# ---------------------------------------------------------------------------
# Per-table column extraction
# ---------------------------------------------------------------------------
#
# We fetch the full attribute list for each entity once with the common
# properties, then issue type-cast queries (Microsoft.Dynamics.CRM.<TypeName>)
# to pull type-specific fields (max length, precision, format, lookup targets,
# option sets). Results are merged back into the base attribute dicts by
# LogicalName.

ATTR_BASE_SELECT = ",".join([
    "LogicalName", "SchemaName", "DisplayName", "Description",
    "AttributeType", "AttributeTypeName", "RequiredLevel",
    "IsValidForCreate", "IsValidForUpdate", "IsValidForRead",
])

CAST_BY_TYPE_NAME: Dict[str, Tuple[str, Tuple[str, ...], Optional[str]]] = {
    "StringType":               ("StringAttributeMetadata",            ("MaxLength", "Format"), None),
    "MemoType":                 ("MemoAttributeMetadata",              ("MaxLength", "Format"), None),
    "IntegerType":              ("IntegerAttributeMetadata",           ("Format",),             None),
    "BigIntType":               ("BigIntAttributeMetadata",            (),                       None),
    "DecimalType":              ("DecimalAttributeMetadata",           ("Precision",),          None),
    "MoneyType":                ("MoneyAttributeMetadata",             ("Precision",),          None),
    "DoubleType":               ("DoubleAttributeMetadata",            ("Precision",),          None),
    "DateTimeType":             ("DateTimeAttributeMetadata",          ("Format",),             None),
    "LookupType":               ("LookupAttributeMetadata",            ("Targets",),            None),
    "CustomerType":             ("LookupAttributeMetadata",            ("Targets",),            None),
    "OwnerType":                ("LookupAttributeMetadata",            ("Targets",),            None),
    "PicklistType":             ("PicklistAttributeMetadata",          (),                       "OptionSet"),
    "StateType":                ("StateAttributeMetadata",             (),                       "OptionSet"),
    "StatusType":               ("StatusAttributeMetadata",            (),                       "OptionSet"),
    "MultiSelectPicklistType":  ("MultiSelectPicklistAttributeMetadata", (),                     "OptionSet"),
    "BooleanType":              ("BooleanAttributeMetadata",           (),                       "OptionSet"),
}


def _fetch_typed(
    client: DataverseClient,
    entity_logical_name: str,
    type_name: str,
    select: List[str],
    expand: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = (
        f"EntityDefinitions(LogicalName='{entity_logical_name}')"
        f"/Attributes/Microsoft.Dynamics.CRM.{type_name}"
    )
    params = {"$select": ",".join(select)}
    if expand:
        params["$expand"] = expand
    return client.get(path, params=params).get("value", [])


def fetch_columns(client: DataverseClient, logical_name: str) -> List[Dict[str, Any]]:
    base = client.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
        params={"$select": ATTR_BASE_SELECT},
    ).get("value", [])
    by_name: Dict[str, Dict[str, Any]] = {a["LogicalName"]: a for a in base}

    # Decide which cast subtypes to fetch based on what's actually in this
    # table. Skips the typical 8-9 wasted round trips per table.
    needed: Dict[str, Tuple[Tuple[str, ...], Optional[str]]] = {}
    for attr in base:
        type_name = (attr.get("AttributeTypeName") or {}).get("Value")
        cast = CAST_BY_TYPE_NAME.get(type_name)
        if cast:
            cast_type, extras, expand = cast
            needed[cast_type] = (extras, expand)

    for cast_type, (extras, expand) in needed.items():
        select = ["LogicalName", *extras]
        items = _fetch_typed(client, logical_name, cast_type, select, expand=expand)

        if cast_type == "BooleanAttributeMetadata":
            # Boolean uses TrueOption/FalseOption instead of Options[].
            for item in items:
                target = by_name.get(item.get("LogicalName"))
                if target is None:
                    continue
                os_obj = item.get("OptionSet") or {}
                target["OptionSet"] = {
                    "Options": [opt for opt in (os_obj.get("TrueOption"),
                                                os_obj.get("FalseOption")) if opt]
                }
            continue

        for item in items:
            target = by_name.get(item.get("LogicalName"))
            if target is None:
                continue
            for field in extras:
                target[field] = item.get(field)
            if expand:
                target[expand] = item.get(expand)

    # Normalize Targets -> LookupTargets for _shape_column.
    for attr in base:
        if "Targets" in attr:
            attr["LookupTargets"] = attr.pop("Targets") or []

    return [_shape_column(attr) for attr in base]


def _shape_column(attr: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "logical_name": attr.get("LogicalName"),
        "schema_name": attr.get("SchemaName"),
        "display_name": _label(attr.get("DisplayName")),
        "description": _label(attr.get("Description")),
        "type": attr.get("AttributeType"),
        "required_level": _required_level(attr.get("RequiredLevel")),
        "is_valid_for_create": attr.get("IsValidForCreate"),
        "is_valid_for_update": attr.get("IsValidForUpdate"),
        "is_valid_for_read": attr.get("IsValidForRead"),
        "max_length": attr.get("MaxLength"),
        "precision": attr.get("Precision"),
        "format": attr.get("Format"),
        "lookup_targets": attr.get("LookupTargets", []),
        "choices": _format_choices(attr.get("OptionSet")),
    }


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------

TABLE_SELECT = ",".join([
    "LogicalName", "SchemaName", "DisplayName", "Description",
    "EntitySetName", "PrimaryIdAttribute", "PrimaryNameAttribute",
])


def _load_checkpoint(partial_path) -> Dict[str, Dict[str, Any]]:
    if not partial_path.is_file():
        return {}
    data = json.loads(partial_path.read_text(encoding="utf-8"))
    return data.get("tables_by_logical_name", {})


def _save_checkpoint(partial_path, env_url: str, done_map: Dict[str, Dict[str, Any]]) -> None:
    """Atomically persist the partial map (write tmp + os.replace)."""
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = partial_path.with_suffix(".partial.json.tmp")
    payload = {
        "environment_url": env_url,
        "checkpoint_at": datetime.now(timezone.utc).isoformat(),
        "tables_by_logical_name": done_map,
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, partial_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg, paths, limits = load_config()
    output_path = paths.metadata_file
    partial_path = paths.metadata_partial_file

    print(f"[auth] env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    print("Listing tables...", file=sys.stderr)
    raw_tables = client.get_all("EntityDefinitions", params={"$select": TABLE_SELECT})
    total = len(raw_tables)

    done_map: Dict[str, Dict[str, Any]] = _load_checkpoint(partial_path)
    if done_map:
        print(
            f"Resuming from checkpoint: {len(done_map)}/{total} tables already done.",
            file=sys.stderr,
        )
    pending = [t for t in raw_tables if t["LogicalName"] not in done_map]
    print(
        f"Fetching column metadata for {len(pending)} remaining tables "
        f"({limits.max_workers} workers in parallel)...",
        file=sys.stderr,
    )

    state_lock = threading.Lock()
    progress = {"done": len(done_map), "since_save": 0}

    def build_table(t: Dict[str, Any]) -> None:
        logical = t["LogicalName"]
        cols = fetch_columns(client, logical)
        record = {
            "logical_name": logical,
            "schema_name": t.get("SchemaName"),
            "display_name": _label(t.get("DisplayName")),
            "description": _label(t.get("Description")),
            "entity_set_name": t.get("EntitySetName"),
            "primary_id_attribute": t.get("PrimaryIdAttribute"),
            "primary_name_attribute": t.get("PrimaryNameAttribute"),
            "columns": cols,
        }
        with state_lock:
            done_map[logical] = record
            progress["done"] += 1
            progress["since_save"] += 1
            print(
                f"  [{progress['done']}/{total}] {logical} "
                f"({len(cols)} columns)",
                file=sys.stderr,
            )
            if progress["since_save"] >= limits.checkpoint_every:
                _save_checkpoint(partial_path, cfg.environment_url, done_map)
                progress["since_save"] = 0
                print(f"  -- checkpoint saved ({len(done_map)} tables)", file=sys.stderr)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=limits.max_workers) as pool:
            for _ in pool.map(build_table, pending):
                pass
    finally:
        with state_lock:
            _save_checkpoint(partial_path, cfg.environment_url, done_map)

    # Build final ordered output in the original EntityDefinitions order.
    tables = [done_map[t["LogicalName"]] for t in raw_tables if t["LogicalName"] in done_map]
    document = {
        "environment_url": cfg.environment_url,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {output_path} ({len(tables)} tables)", file=sys.stderr)

    if len(tables) == total and partial_path.is_file():
        partial_path.unlink()


if __name__ == "__main__":
    main()
