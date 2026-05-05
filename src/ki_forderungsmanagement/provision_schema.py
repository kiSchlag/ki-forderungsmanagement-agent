"""Provision Dataverse tables and columns from scenario_table_shortlist.json.

Reads the curated shortlist (produced by ``ki_forderungsmanagement.build_shortlist``)
and creates all custom tables, custom columns, and lookup relationships it
describes via the Dataverse Web API.

Default mode is dry-run; pass ``--apply`` to actually send POST requests.

Sources (Microsoft Learn):
  - Service protection limits:
      https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits
  - Create tables:
      https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-entity-definitions-using-web-api
  - Create columns:
      https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-column-definitions-using-web-api
  - Create relationships:
      https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-update-entity-relationships-using-web-api

Usage::

    python -m ki_forderungsmanagement.provision_schema             # dry-run
    python -m ki_forderungsmanagement.provision_schema --apply     # apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .config import load_config
from .http_client import DataverseClient


LANGUAGE_CODE = 1033

DEFAULT_SOLUTION = "mueller_demo_solution"
DEFAULT_PUBLISHER_UNIQUE = "mueller_publisher"
DEFAULT_PREFIX = "mueller"
# 5-digit publisher option-value prefix per Microsoft docs; Dataverse multiplies
# this into the 100000000+ value range automatically when used via maker UI,
# but for direct Web API POST we choose explicit values starting at:
DEFAULT_OPTION_VALUE_BASE = 727000000


# ---------------------------------------------------------------------------
# Label and payload helpers
# ---------------------------------------------------------------------------

def label(text: str) -> dict:
    """Build a Microsoft.Dynamics.CRM.Label dict."""
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.Label",
        "LocalizedLabels": [{
            "@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
            "Label": text or "",
            "LanguageCode": LANGUAGE_CODE,
        }],
    }


def required_level(level: str = "None") -> dict:
    return {
        "Value": level,
        "CanBeChanged": True,
        "ManagedPropertyLogicalName": "canmodifyrequirementlevelsettings",
    }


def derive_display_name(logical_name: str, prefix: str) -> str:
    name = logical_name
    pfx = f"{prefix}_"
    if name.lower().startswith(pfx):
        name = name[len(pfx):]
    name = name.replace("_", " ").strip() or logical_name
    return " ".join(w.capitalize() for w in name.split())


# ---------------------------------------------------------------------------
# Attribute payload builders, one per Dataverse type
# ---------------------------------------------------------------------------

def build_string_attribute(logical_name, display_name, description,
                            max_length=100, format_name="Text",
                            is_primary_name=False):
    payload = {
        "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
        "AttributeType": "String",
        "AttributeTypeName": {"Value": "StringType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "MaxLength": max_length,
        "FormatName": {"Value": format_name},
    }
    if is_primary_name:
        payload["IsPrimaryName"] = True
    return payload


def build_memo_attribute(logical_name, display_name, description, max_length=4000):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.MemoAttributeMetadata",
        "AttributeType": "Memo",
        "AttributeTypeName": {"Value": "MemoType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "MaxLength": max_length,
        "Format": "TextArea",
        "ImeMode": "Disabled",
    }


def build_integer_attribute(logical_name, display_name, description):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata",
        "AttributeType": "Integer",
        "AttributeTypeName": {"Value": "IntegerType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "Format": "None",
        "MinValue": -2147483648,
        "MaxValue": 2147483647,
    }


def build_decimal_attribute(logical_name, display_name, description, precision=2):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
        "AttributeType": "Decimal",
        "AttributeTypeName": {"Value": "DecimalType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "Precision": precision,
        "MinValue": -100000000000,
        "MaxValue": 100000000000,
    }


def build_money_attribute(logical_name, display_name, description):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.MoneyAttributeMetadata",
        "AttributeType": "Money",
        "AttributeTypeName": {"Value": "MoneyType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "PrecisionSource": 2,
    }


def build_datetime_attribute(logical_name, display_name, description, fmt="DateAndTime"):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
        "AttributeType": "DateTime",
        "AttributeTypeName": {"Value": "DateTimeType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "Format": fmt,
    }


def build_boolean_attribute(logical_name, display_name, description):
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata",
        "AttributeType": "Boolean",
        "AttributeTypeName": {"Value": "BooleanType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "DefaultValue": False,
        "OptionSet": {
            "TrueOption": {"Value": 1, "Label": label("True")},
            "FalseOption": {"Value": 0, "Label": label("False")},
            "OptionSetType": "Boolean",
        },
    }


# Match a slash-separated list at the start of a string OR inside parentheses,
# with at least 2 items. Allows German letters via the À-ÿ range.
OPTIONSET_LIST_PATTERN = re.compile(
    r'(?:^|\()\s*('
    r'[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_0-9]*'
    r'(?:\s*/\s*[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ_0-9]*)+'
    r')\s*(?:\)|-|$)'
)


def parse_optionset_options(purpose: str) -> Optional[List[str]]:
    if not purpose:
        return None
    m = OPTIONSET_LIST_PATTERN.search(purpose)
    if not m:
        return None
    options = [s.strip() for s in m.group(1).split("/") if s.strip()]
    return options if len(options) >= 2 else None


def build_picklist_attribute(logical_name, display_name, description,
                              option_labels, value_base=DEFAULT_OPTION_VALUE_BASE):
    options = []
    if not option_labels:
        option_labels = ["Default"]
    for i, lbl in enumerate(option_labels):
        options.append({"Value": value_base + i, "Label": label(lbl)})
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
        "AttributeType": "Picklist",
        "AttributeTypeName": {"Value": "PicklistType"},
        "SchemaName": logical_name,
        "DisplayName": label(display_name),
        "Description": label(description or ""),
        "RequiredLevel": required_level(),
        "OptionSet": {
            "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
            "Options": options,
            "IsGlobal": False,
            "OptionSetType": "Picklist",
        },
    }


def build_attribute(spec: dict, prefix: str, is_primary_name: bool = False):
    """Dispatch by 'type' field to the right builder. Returns None for skip."""
    t = spec.get("type")
    ln = spec["logical_name"]
    purpose = spec.get("purpose", "")
    display = derive_display_name(ln, prefix)
    if t == "String":
        return build_string_attribute(ln, display, purpose,
                                       max_length=spec.get("max_length", 100),
                                       is_primary_name=is_primary_name)
    if t == "Memo":
        return build_memo_attribute(ln, display, purpose)
    if t == "Integer":
        return build_integer_attribute(ln, display, purpose)
    if t == "Decimal":
        return build_decimal_attribute(ln, display, purpose)
    if t == "Money":
        return build_money_attribute(ln, display, purpose)
    if t == "DateTime":
        return build_datetime_attribute(ln, display, purpose)
    if t == "Boolean":
        return build_boolean_attribute(ln, display, purpose)
    if t == "OptionSet":
        opts = parse_optionset_options(purpose)
        if not opts:
            print(f"    ! WARN: no parseable options for {ln} - using placeholder ['Default']",
                  file=sys.stderr)
        return build_picklist_attribute(ln, display, purpose, opts)
    if t in ("Lookup", "Uniqueidentifier"):
        return None  # Lookup handled separately; PK is auto-generated
    raise ValueError(f"Unsupported attribute type {t!r} for column {ln}")


# ---------------------------------------------------------------------------
# Lookup (one-to-many) relationship builder
# ---------------------------------------------------------------------------

def build_lookup_relationship(referencing_entity, referenced_entity,
                               referenced_attribute, lookup_logical,
                               lookup_display, lookup_description, prefix):
    ref_bare = referencing_entity[len(prefix) + 1:] if referencing_entity.startswith(f"{prefix}_") else referencing_entity
    target_bare = referenced_entity[len(prefix) + 1:] if referenced_entity.startswith(f"{prefix}_") else referenced_entity
    lookup_bare = lookup_logical[len(prefix) + 1:] if lookup_logical.startswith(f"{prefix}_") else lookup_logical
    schema = f"{prefix}_{ref_bare}_{target_bare}_{lookup_bare}"
    schema = re.sub(r"_{2,}", "_", schema).strip("_")
    if len(schema) > 100:
        schema = schema[:100].rstrip("_")
    return {
        "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        "SchemaName": schema,
        "ReferencedEntity": referenced_entity,
        "ReferencedAttribute": referenced_attribute,
        "ReferencingEntity": referencing_entity,
        "AssociatedMenuConfiguration": {
            "Behavior": "UseCollectionName",
            "Group": "Details",
            "Order": 10000,
            "Label": label(""),
        },
        "CascadeConfiguration": {
            "Assign": "NoCascade",
            "Delete": "RemoveLink",
            "Merge": "NoCascade",
            "Reparent": "NoCascade",
            "Share": "NoCascade",
            "Unshare": "NoCascade",
        },
        "Lookup": {
            "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
            "AttributeType": "Lookup",
            "AttributeTypeName": {"Value": "LookupType"},
            "SchemaName": lookup_logical,
            "DisplayName": label(lookup_display),
            "Description": label(lookup_description or ""),
            "RequiredLevel": required_level(),
        },
    }


def infer_lookup_target(column_spec: dict, prefix: str) -> str:
    """Derive the referenced entity logical name from the column's purpose / name.

    Tries the explicit 'Lookup -> <entity>' pattern first, then falls back to
    naming heuristics for the well-known cases in our shortlist.
    """
    purpose = column_spec.get("purpose", "")
    m = re.search(r'Lookup\s*->\s*([a-z_][a-z0-9_]*)', purpose, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    ln = column_spec["logical_name"]
    bare = ln[len(prefix) + 1:] if ln.startswith(f"{prefix}_") else ln
    name_to_target = {
        "customerid": "account",
        "accountid": "account",
        "parentid": "account",
        "contactid": "contact",
        "acceptedby": "contact",
        "invoiceid": "invoice",
        "orderid": "salesorder",
        "salesorderid": "salesorder",
        "currencyid": "transactioncurrency",
        "emailid": "email",
        "letterid": "letter",
        "overrideby": "systemuser",
        "routedtouserid": "systemuser",
        "keyaccountmanager": "systemuser",
        "holdoverrideby": "systemuser",
        "paymentplanid": f"{prefix}_paymentplan",
        "paymenteventid": f"{prefix}_paymentevent",
    }
    if bare in name_to_target:
        return name_to_target[bare]
    raise ValueError(f"Cannot infer lookup target for {ln} (purpose={purpose!r})")


# ---------------------------------------------------------------------------
# Idempotency probes
# ---------------------------------------------------------------------------

def probe_publisher(client: DataverseClient, unique_name: str) -> Optional[dict]:
    body = client.get("publishers", params={
        "$filter": f"uniquename eq '{unique_name}'",
        "$select": "publisherid",
    })
    items = (body or {}).get("value", [])
    return items[0] if items else None


def probe_solution(client: DataverseClient, unique_name: str) -> Optional[dict]:
    body = client.get("solutions", params={
        "$filter": f"uniquename eq '{unique_name}'",
        "$select": "solutionid",
    })
    items = (body or {}).get("value", [])
    return items[0] if items else None


def probe_table(client: DataverseClient, logical_name: str) -> Optional[dict]:
    return client.get(
        f"EntityDefinitions(LogicalName='{logical_name}')",
        params={"$select": "LogicalName,PrimaryIdAttribute"},
        allow_404=True,
    )


def probe_attribute(client: DataverseClient, table_logical: str,
                    column_logical: str) -> Optional[dict]:
    return client.get(
        f"EntityDefinitions(LogicalName='{table_logical}')/Attributes(LogicalName='{column_logical}')",
        params={"$select": "LogicalName"},
        allow_404=True,
    )


def probe_relationship(client: DataverseClient, schema_name: str) -> Optional[dict]:
    return client.get(
        f"RelationshipDefinitions(SchemaName='{schema_name}')",
        params={"$select": "SchemaName"},
        allow_404=True,
    )


# ---------------------------------------------------------------------------
# Phase 0: publisher + solution
# ---------------------------------------------------------------------------

def ensure_publisher(client, unique_name, prefix, dry_run) -> Optional[str]:
    print(f"[probe]   publisher '{unique_name}' ...", end=" ", file=sys.stderr)
    existing = probe_publisher(client, unique_name)
    if existing:
        print(f"EXISTS (id={existing['publisherid']})", file=sys.stderr)
        return existing["publisherid"]
    print("missing", file=sys.stderr)
    if dry_run:
        print(f"[dry-run] would POST /publishers (prefix='{prefix}', "
              f"customizationoptionvalueprefix={DEFAULT_OPTION_VALUE_BASE // 10000})",
              file=sys.stderr)
        return None
    body = {
        "uniquename": unique_name,
        "friendlyname": unique_name.replace("_", " ").title(),
        "description": "Publisher for the Mueller AR demo provisioning script",
        "customizationprefix": prefix,
        "customizationoptionvalueprefix": DEFAULT_OPTION_VALUE_BASE // 10000,
    }
    r = client.post("publishers", json=body)
    entity_id = r.headers.get("OData-EntityId", "")
    m = re.search(r"publishers\(([0-9a-f-]+)\)", entity_id)
    pub_id = m.group(1) if m else "?"
    print(f"[create]  POST /publishers ... 204 (id={pub_id})", file=sys.stderr)
    return pub_id


def ensure_solution(client, unique_name, publisher_id, dry_run) -> Optional[str]:
    print(f"[probe]   solution '{unique_name}' ...", end=" ", file=sys.stderr)
    existing = probe_solution(client, unique_name)
    if existing:
        print(f"EXISTS (id={existing['solutionid']})", file=sys.stderr)
        return existing["solutionid"]
    print("missing", file=sys.stderr)
    if dry_run:
        print(f"[dry-run] would POST /solutions linking to publisher {publisher_id or '<would-be-created>'}",
              file=sys.stderr)
        return None
    if publisher_id is None:
        sys.exit("Cannot create solution: publisher does not exist (and not in dry-run).")
    body = {
        "uniquename": unique_name,
        "friendlyname": unique_name.replace("_", " ").title(),
        "description": "Mueller AR demo solution",
        "version": "1.0.0.0",
        "publisherid@odata.bind": f"publishers({publisher_id})",
    }
    r = client.post("solutions", json=body)
    entity_id = r.headers.get("OData-EntityId", "")
    m = re.search(r"solutions\(([0-9a-f-]+)\)", entity_id)
    sol_id = m.group(1) if m else "?"
    print(f"[create]  POST /solutions ... 204 (id={sol_id})", file=sys.stderr)
    return sol_id


# ---------------------------------------------------------------------------
# Phase 1: custom tables (scalars only)
# ---------------------------------------------------------------------------

def create_custom_tables(client, custom_tables, prefix, solution_unique_name, dry_run):
    headers = {"MSCRM.SolutionUniqueName": solution_unique_name}
    created = skipped = 0
    total = len(custom_tables)
    for idx, ct in enumerate(custom_tables, 1):
        ln = ct["proposed_logical_name"]
        print(f"[1.{idx}/{total}] {ln} ...", end=" ", file=sys.stderr)
        if probe_table(client, ln):
            print("EXISTS", file=sys.stderr)
            skipped += 1
            continue
        print("missing", end=" ", file=sys.stderr)

        scalars = []
        primary_name_present = False
        for col in ct["proposed_columns"]:
            if col["type"] in ("Lookup", "Uniqueidentifier"):
                continue
            is_primary = (col["logical_name"] == f"{prefix}_name")
            attr = build_attribute(col, prefix=prefix, is_primary_name=is_primary)
            if attr is None:
                continue
            if is_primary:
                primary_name_present = True
            scalars.append(attr)

        if not primary_name_present:
            scalars.insert(0, build_string_attribute(
                f"{prefix}_name",
                "Name",
                f"Display name for {ln}",
                max_length=100,
                is_primary_name=True,
            ))

        body = {
            "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
            "SchemaName": ln,
            "DisplayName": label(derive_display_name(ln, prefix)),
            "DisplayCollectionName": label(derive_display_name(ln, prefix) + "s"),
            "Description": label((ct.get("purpose") or "")[:1000]),
            "OwnershipType": "UserOwned",
            "IsActivity": False,
            "HasActivities": False,
            "HasNotes": False,
            "Attributes": scalars,
        }
        if dry_run:
            print(f"-> would POST /EntityDefinitions ({len(scalars)} scalar cols)",
                  file=sys.stderr)
            created += 1
            continue
        client.post("EntityDefinitions", json=body, extra_headers=headers)
        print(f"-> 204 ({len(scalars)} cols inline)", file=sys.stderr)
        created += 1
    return created, skipped


# ---------------------------------------------------------------------------
# Phase 2: lookups inside custom tables
# ---------------------------------------------------------------------------

def collect_in_table_lookups(custom_tables, prefix):
    out = []
    for ct in custom_tables:
        referencing = ct["proposed_logical_name"]
        for col in ct["proposed_columns"]:
            if col["type"] != "Lookup":
                continue
            try:
                target = infer_lookup_target(col, prefix)
            except ValueError as e:
                print(f"  ! WARN: {e}", file=sys.stderr)
                continue
            out.append({
                "referencing": referencing,
                "referenced": target,
                "lookup_logical": col["logical_name"],
                "purpose": col.get("purpose", ""),
            })
    # System-table targets first, then mueller_* targets - so paymentplaninstallment.mueller_paymenteventid
    # comes after mueller_paymentevent has been created.
    out.sort(key=lambda x: (x["referenced"].startswith(f"{prefix}_"), x["referencing"], x["lookup_logical"]))
    return out


def resolve_parent_pk(client, parent_logical, cache, dry_run):
    if parent_logical in cache:
        return cache[parent_logical]
    t = probe_table(client, parent_logical)
    if t is None:
        if dry_run:
            # Parent doesn't exist yet (we're in dry-run before phase 1) -
            # assume the conventional <table>id PK so Phase 2/4 can still print plans.
            pk = parent_logical + "id"
        else:
            return None
    else:
        pk = t["PrimaryIdAttribute"]
    cache[parent_logical] = pk
    return pk


def create_in_table_lookups(client, custom_tables, prefix, solution_unique_name,
                            dry_run, parent_pk_cache):
    headers = {"MSCRM.SolutionUniqueName": solution_unique_name}
    created = skipped = 0
    lookups = collect_in_table_lookups(custom_tables, prefix)
    total = len(lookups)
    for idx, lk in enumerate(lookups, 1):
        pk = resolve_parent_pk(client, lk["referenced"], parent_pk_cache, dry_run)
        if pk is None:
            print(f"[2.{idx}/{total}] {lk['referencing']}.{lk['lookup_logical']} -> {lk['referenced']} ... TARGET MISSING - skip",
                  file=sys.stderr)
            skipped += 1
            continue
        rel = build_lookup_relationship(
            referencing_entity=lk["referencing"],
            referenced_entity=lk["referenced"],
            referenced_attribute=pk,
            lookup_logical=lk["lookup_logical"],
            lookup_display=derive_display_name(lk["lookup_logical"], prefix),
            lookup_description=lk["purpose"],
            prefix=prefix,
        )
        print(f"[2.{idx}/{total}] {lk['referencing']}.{lk['lookup_logical']} -> {lk['referenced']} ...",
              end=" ", file=sys.stderr)
        if not dry_run and probe_relationship(client, rel["SchemaName"]):
            print("REL EXISTS", file=sys.stderr)
            skipped += 1
            continue
        if not dry_run and probe_attribute(client, lk["referencing"], lk["lookup_logical"]):
            print("ATTR EXISTS", file=sys.stderr)
            skipped += 1
            continue
        print("missing", end=" ", file=sys.stderr)
        if dry_run:
            print(f"-> would POST /RelationshipDefinitions (schema={rel['SchemaName']})",
                  file=sys.stderr)
            created += 1
            continue
        client.post("RelationshipDefinitions", json=rel, extra_headers=headers)
        print("-> 204", file=sys.stderr)
        created += 1
    return created, skipped


# ---------------------------------------------------------------------------
# Phase 3: scalar custom columns on system tables
# ---------------------------------------------------------------------------

def create_system_table_columns(client, custom_columns, prefix, solution_unique_name, dry_run):
    headers = {"MSCRM.SolutionUniqueName": solution_unique_name}
    created = skipped = 0
    scalar_cols = [c for c in custom_columns if c["type"] not in ("Lookup", "Uniqueidentifier")]
    total = len(scalar_cols)
    for idx, col in enumerate(scalar_cols, 1):
        target = col["target_table"]
        ln = col["new_column_logical_name"]
        print(f"[3.{idx}/{total}] {target}.{ln} ...", end=" ", file=sys.stderr)
        if not dry_run and probe_attribute(client, target, ln):
            print("EXISTS", file=sys.stderr)
            skipped += 1
            continue
        spec = dict(col, logical_name=ln)
        attr = build_attribute(spec, prefix=prefix)
        if attr is None:
            print("SKIP (unsupported)", file=sys.stderr)
            skipped += 1
            continue
        print("missing", end=" ", file=sys.stderr)
        if dry_run:
            print(f"-> would POST /EntityDefinitions(LogicalName='{target}')/Attributes",
                  file=sys.stderr)
            created += 1
            continue
        client.post(f"EntityDefinitions(LogicalName='{target}')/Attributes",
                     json=attr, extra_headers=headers)
        print("-> 204", file=sys.stderr)
        created += 1
    return created, skipped


# ---------------------------------------------------------------------------
# Phase 4: lookup custom columns on system tables
# ---------------------------------------------------------------------------

def create_system_table_lookups(client, custom_columns, prefix, solution_unique_name,
                                 dry_run, parent_pk_cache):
    headers = {"MSCRM.SolutionUniqueName": solution_unique_name}
    created = skipped = 0
    lookup_cols = [c for c in custom_columns if c["type"] == "Lookup"]
    total = len(lookup_cols)
    for idx, col in enumerate(lookup_cols, 1):
        target = col["target_table"]
        ln = col["new_column_logical_name"]
        spec = dict(col, logical_name=ln)
        try:
            referenced = infer_lookup_target(spec, prefix)
        except ValueError as e:
            print(f"[4.{idx}/{total}] {target}.{ln} ... ! {e}", file=sys.stderr)
            skipped += 1
            continue
        pk = resolve_parent_pk(client, referenced, parent_pk_cache, dry_run)
        if pk is None:
            print(f"[4.{idx}/{total}] {target}.{ln} -> {referenced} ... TARGET MISSING - skip",
                  file=sys.stderr)
            skipped += 1
            continue
        rel = build_lookup_relationship(
            referencing_entity=target,
            referenced_entity=referenced,
            referenced_attribute=pk,
            lookup_logical=ln,
            lookup_display=derive_display_name(ln, prefix),
            lookup_description=col.get("purpose", ""),
            prefix=prefix,
        )
        print(f"[4.{idx}/{total}] {target}.{ln} -> {referenced} ...", end=" ", file=sys.stderr)
        if not dry_run and probe_relationship(client, rel["SchemaName"]):
            print("REL EXISTS", file=sys.stderr)
            skipped += 1
            continue
        if not dry_run and probe_attribute(client, target, ln):
            print("ATTR EXISTS", file=sys.stderr)
            skipped += 1
            continue
        print("missing", end=" ", file=sys.stderr)
        if dry_run:
            print(f"-> would POST /RelationshipDefinitions (schema={rel['SchemaName']})",
                  file=sys.stderr)
            created += 1
            continue
        client.post("RelationshipDefinitions", json=rel, extra_headers=headers)
        print("-> 204", file=sys.stderr)
        created += 1
    return created, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg, paths, limits = load_config()

    parser = argparse.ArgumentParser(
        description="Provision Dataverse tables/columns from scenario_table_shortlist.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true",
                         help="Actually send POST requests (default is dry-run)")
    parser.add_argument("--shortlist", default=str(paths.shortlist_file),
                         help=f"Path to shortlist JSON (default: {paths.shortlist_file})")
    parser.add_argument("--solution", default=DEFAULT_SOLUTION,
                         help=f"Solution unique name (default: {DEFAULT_SOLUTION})")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                         help=f"Publisher customization prefix (default: {DEFAULT_PREFIX})")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"=== ki_forderungsmanagement.provision_schema ({mode}) ===", file=sys.stderr)

    shortlist_path = Path(args.shortlist)
    if not shortlist_path.is_file():
        sys.exit(f"Shortlist not found: {shortlist_path}")
    shortlist = json.loads(shortlist_path.read_text(encoding="utf-8"))

    print(f"[auth]    env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    publisher_unique = f"{args.prefix}_publisher"
    publisher_id = ensure_publisher(client, publisher_unique, args.prefix, dry_run)
    ensure_solution(client, args.solution, publisher_id, dry_run)

    parent_pk_cache: Dict[str, str] = {}

    print("\n=== Phase 1: custom tables (scalars only) ===", file=sys.stderr)
    p1_c, p1_s = create_custom_tables(client, shortlist["custom_tables_needed"],
                                        args.prefix, args.solution, dry_run)

    print("\n=== Phase 2: lookups inside custom tables ===", file=sys.stderr)
    p2_c, p2_s = create_in_table_lookups(client, shortlist["custom_tables_needed"],
                                          args.prefix, args.solution, dry_run, parent_pk_cache)

    print("\n=== Phase 3: scalar custom columns on system tables ===", file=sys.stderr)
    p3_c, p3_s = create_system_table_columns(client, shortlist["custom_columns_to_add"],
                                               args.prefix, args.solution, dry_run)

    print("\n=== Phase 4: lookup custom columns on system tables ===", file=sys.stderr)
    p4_c, p4_s = create_system_table_lookups(client, shortlist["custom_columns_to_add"],
                                               args.prefix, args.solution, dry_run, parent_pk_cache)

    tables_created = p1_c
    tables_skipped = p1_s
    cols_created = p2_c + p3_c + p4_c
    cols_skipped = p2_s + p3_s + p4_s
    suffix = " (dry-run, no changes were made)" if dry_run else ""
    print(f"\nCreated {tables_created} custom tables, {cols_created} custom columns. "
          f"Skipped {tables_skipped} existing tables, {cols_skipped} existing columns.{suffix}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
