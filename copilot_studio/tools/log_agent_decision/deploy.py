from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(REPO_SRC_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ki_forderungsmanagement.config import load_config
from ki_forderungsmanagement.http_client import DataverseClient

import flow_definition


FLOW_NAME = "LogAgentDecision"
FLOW_DESCRIPTION = (
    "Append an audit-trail row to mueller_agentdecision for the Müller AR scenario. "
    "Required by Instruction I15. "
    "Use after every action that affects a customer record (dunning, hold, payment plan, escalation). "
    "Do NOT use for read-only steps. "
    "Returns the GUID of the created mueller_agentdecision record."
)
SOLUTION_UNIQUE_NAME = "mueller_demo_solution"
CONNECTION_REFERENCE_LOGICAL_NAME = "new_sharedcommondataserviceforapps_14a83"

CATEGORY_MODERN_FLOW = 5
TYPE_DEFINITION = 1
PRIMARY_ENTITY_NONE = "none"
STATECODE_DRAFT = 0
STATECODE_ACTIVATED = 1

SOLUTION_COMPONENT_TYPE_WORKFLOW = 29


def workflow_id_by_name(client: DataverseClient, name: str) -> Optional[str]:
    safe = name.replace("'", "''")
    body = client.get(
        "workflows",
        params={
            "$filter": f"name eq '{safe}' and category eq {CATEGORY_MODERN_FLOW}",
            "$select": "workflowid,name,statecode",
            "$top": "1",
        },
    )
    items = (body or {}).get("value", [])
    return items[0]["workflowid"] if items else None


def solution_id_by_unique_name(client: DataverseClient, unique_name: str) -> Optional[str]:
    safe = unique_name.replace("'", "''")
    body = client.get(
        "solutions",
        params={
            "$filter": f"uniquename eq '{safe}'",
            "$select": "solutionid",
            "$top": "1",
        },
    )
    items = (body or {}).get("value", [])
    return items[0]["solutionid"] if items else None


def workflow_in_solution(client: DataverseClient, solution_id: str, workflow_id: str) -> bool:
    body = client.get(
        "solutioncomponents",
        params={
            "$filter": (
                f"_solutionid_value eq {solution_id} "
                f"and componenttype eq {SOLUTION_COMPONENT_TYPE_WORKFLOW} "
                f"and objectid eq {workflow_id}"
            ),
            "$select": "solutioncomponentid",
            "$top": "1",
        },
    )
    return bool((body or {}).get("value", []))


def create_workflow(
    client: DataverseClient,
    solution_unique_name: str,
    name: str,
    description: str,
    clientdata_string: str,
) -> str:
    payload = {
        "category": CATEGORY_MODERN_FLOW,
        "name": name,
        "type": TYPE_DEFINITION,
        "description": description,
        "primaryentity": PRIMARY_ENTITY_NONE,
        "clientdata": clientdata_string,
    }
    response = client.post(
        "workflows",
        json=payload,
        extra_headers={"MSCRM.SolutionUniqueName": solution_unique_name},
    )
    location = response.headers.get("OData-EntityId", "")
    match = re.search(r"workflows\(([0-9a-fA-F-]{36})\)", location)
    if not match:
        raise RuntimeError(
            f"workflow created but could not extract GUID from OData-EntityId: {location!r}"
        )
    return match.group(1).lower()


def add_workflow_to_solution(
    client: DataverseClient,
    solution_unique_name: str,
    workflow_id: str,
) -> None:
    payload = {
        "ComponentId": workflow_id,
        "ComponentType": SOLUTION_COMPONENT_TYPE_WORKFLOW,
        "SolutionUniqueName": solution_unique_name,
        "AddRequiredComponents": False,
        "DoNotIncludeSubcomponents": False,
        "IncludedComponentSettingsValues": None,
    }
    client.post("AddSolutionComponent", json=payload)


def patch_workflow_state(client: DataverseClient, workflow_id: str, statecode: int) -> None:
    statuscode = 1 if statecode == STATECODE_DRAFT else 2
    client.patch(
        f"workflows({workflow_id})",
        json={"statecode": statecode, "statuscode": statuscode},
        extra_headers={"If-Match": "*"},
    )


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy the Log Agent Decision flow to Dataverse",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", default=FLOW_NAME)
    parser.add_argument("--solution", default=SOLUTION_UNIQUE_NAME)
    parser.add_argument("--connection-reference", default=CONNECTION_REFERENCE_LOGICAL_NAME)
    parser.add_argument("--description", default=FLOW_DESCRIPTION)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()

    if args.dry_run:
        clientdata = flow_definition.build_clientdata(args.connection_reference)
        print(json.dumps(clientdata, indent=2, ensure_ascii=False))
        return 0

    cfg, _, limits = load_config()
    print(f"[auth] env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    existing_id = workflow_id_by_name(client, args.name)
    if existing_id:
        print(f"Workflow {args.name!r} already exists at {existing_id}", file=sys.stderr)
        if args.activate:
            patch_workflow_state(client, existing_id, STATECODE_ACTIVATED)
            print(f"  activated (statecode=1)", file=sys.stderr)
        return 0

    solution_id = solution_id_by_unique_name(client, args.solution)
    if not solution_id:
        print(f"FATAL: solution {args.solution!r} not found", file=sys.stderr)
        return 2

    clientdata_string = flow_definition.build_clientdata_string(args.connection_reference)
    workflow_id = create_workflow(
        client,
        args.solution,
        args.name,
        args.description,
        clientdata_string,
    )
    print(f"Created workflow {workflow_id}", file=sys.stderr)

    if not workflow_in_solution(client, solution_id, workflow_id):
        add_workflow_to_solution(client, args.solution, workflow_id)
        print(f"  added to solution {args.solution}", file=sys.stderr)

    if args.activate:
        patch_workflow_state(client, workflow_id, STATECODE_ACTIVATED)
        print(f"  activated (statecode=1)", file=sys.stderr)
    else:
        print(
            f"  left as draft (statecode=0). Pass --activate or PATCH manually.",
            file=sys.stderr,
        )

    print(
        "Tool registration: open Copilot Studio Maker → AR-Agent → Tools → "
        f"Add a tool → Flow → {args.name!r}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
