from __future__ import annotations

import json


WORKFLOW_DEFINITION_SCHEMA_URL = (
    "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/"
    "2016-06-01/workflowdefinition.json#"
)
CONTENT_VERSION = "1.0.0.0"
SCHEMA_VERSION = "1.0.0.0"

CONNECTOR_LOGICAL_NAME = "shared_commondataserviceforapps"
CONNECTOR_API_ID = "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"
CREATE_OPERATION_ID = "CreateRecord"

TARGET_ENTITY_SET_NAME = "mueller_agentdecisions"

CREATE_ACTION_KEY = "Create_Decision_Record"
RESPONSE_ACTION_KEY = "Respond_to_the_agent"
TRIGGER_KEY = "manual"


def _text_property(title: str, description: str) -> dict:
    return {
        "title": title,
        "description": description,
        "type": "string",
        "x-ms-content-hint": "TEXT",
        "x-ms-dynamically-added": True,
    }


def _number_property(title: str, description: str) -> dict:
    return {
        "title": title,
        "description": description,
        "type": "number",
        "x-ms-content-hint": "NUMBER",
        "x-ms-dynamically-added": True,
    }


def build_trigger_input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "customer_id": _text_property(
                "CustomerId",
                "GUID of the account record this decision concerns.",
            ),
            "reasoning": _text_property(
                "Reasoning",
                "Plain-language reasoning the agent recorded for this decision.",
            ),
            "confidence_score": _number_property(
                "ConfidenceScore",
                "Model confidence in [0.0, 1.0]. Values below 0.6 trigger human routing per Instruction I10.",
            ),
            "model_name": _text_property(
                "ModelName",
                "Identifier of the model that produced the decision (for example gpt-4o).",
            ),
            "prompt_version": _text_property(
                "PromptVersion",
                "Optional. Version tag of the prompt template used.",
            ),
            "inputs_hash": _text_property(
                "InputsHash",
                "Optional. Hash of the inputs that fed the decision, for reproducibility.",
            ),
            "inputs_payload": _text_property(
                "InputsPayload",
                "Optional. JSON snapshot of the inputs that fed the decision.",
            ),
            "invoice_id": _text_property(
                "InvoiceId",
                "Optional. GUID of the invoice this decision concerns.",
            ),
            "order_id": _text_property(
                "OrderId",
                "Optional. GUID of the sales order this decision concerns.",
            ),
            "decided_at": _text_property(
                "DecidedAt",
                "Optional. ISO-8601 timestamp. Empty value defaults to utcNow().",
            ),
            "override_by_id": _text_property(
                "OverrideById",
                "Optional. GUID of a systemuser who overrode the decision.",
            ),
            "override_reason": _text_property(
                "OverrideReason",
                "Optional. Why the human overrode the decision.",
            ),
        },
        "required": ["customer_id", "reasoning", "confidence_score", "model_name"],
    }


def build_response_output_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "status": _text_property(
                "Status",
                "Logged when the audit row was created successfully.",
            ),
            "decision_id": _text_property(
                "DecisionId",
                "GUID of the newly created mueller_agentdecision record.",
            ),
        },
        "additionalProperties": {},
    }


def build_trigger() -> dict:
    return {
        TRIGGER_KEY: {
            "type": "Request",
            "kind": "Skills",
            "inputs": {"schema": build_trigger_input_schema()},
            "metadata": {},
        }
    }


def _trigger_text(name: str) -> str:
    return f"@triggerBody()?['{name}']"


def _odata_bind_optional(input_name: str, entity_set: str) -> str:
    body_ref = f"triggerBody()?['{input_name}']"
    return (
        f"@if(empty({body_ref}), null, "
        f"concat('/{entity_set}(', {body_ref}, ')'))"
    )


def _odata_bind_required(input_name: str, entity_set: str) -> str:
    body_ref = f"triggerBody()?['{input_name}']"
    return f"@concat('/{entity_set}(', {body_ref}, ')')"


def _decided_at_expression() -> str:
    body_ref = "triggerBody()?['decided_at']"
    return f"@if(empty({body_ref}), utcNow(), {body_ref})"


def build_create_decision_action() -> dict:
    return {
        "type": "OpenApiConnection",
        "inputs": {
            "host": {
                "apiId": CONNECTOR_API_ID,
                "operationId": CREATE_OPERATION_ID,
                "connectionName": CONNECTOR_LOGICAL_NAME,
            },
            "parameters": {
                "entityName": TARGET_ENTITY_SET_NAME,
                "item/mueller_reasoning": _trigger_text("reasoning"),
                "item/mueller_confidencescore": _trigger_text("confidence_score"),
                "item/mueller_modelname": _trigger_text("model_name"),
                "item/mueller_promptversion": _trigger_text("prompt_version"),
                "item/mueller_inputshash": _trigger_text("inputs_hash"),
                "item/mueller_inputspayload": _trigger_text("inputs_payload"),
                "item/mueller_overridereason": _trigger_text("override_reason"),
                "item/mueller_decidedat": _decided_at_expression(),
                "item/mueller_customerid@odata.bind": _odata_bind_required(
                    "customer_id", "accounts"
                ),
                "item/mueller_invoiceid@odata.bind": _odata_bind_optional(
                    "invoice_id", "invoices"
                ),
                "item/mueller_orderid@odata.bind": _odata_bind_optional(
                    "order_id", "salesorders"
                ),
                "item/mueller_overrideby@odata.bind": _odata_bind_optional(
                    "override_by_id", "systemusers"
                ),
            },
            "authentication": "@parameters('$authentication')",
        },
        "runAfter": {},
        "metadata": {},
    }


def build_respond_action() -> dict:
    decision_id_expression = (
        "@{outputs('"
        + CREATE_ACTION_KEY
        + "')?['body/mueller_agentdecisionid']}"
    )
    return {
        "type": "Response",
        "kind": "Skills",
        "inputs": {
            "schema": build_response_output_schema(),
            "statusCode": 200,
            "body": {
                "status": "Logged",
                "decision_id": decision_id_expression,
            },
        },
        "runAfter": {CREATE_ACTION_KEY: ["Succeeded"]},
        "metadata": {},
    }


def build_workflow_definition() -> dict:
    return {
        "$schema": WORKFLOW_DEFINITION_SCHEMA_URL,
        "contentVersion": CONTENT_VERSION,
        "parameters": {
            "$connections": {"defaultValue": {}, "type": "Object"},
            "$authentication": {"defaultValue": {}, "type": "SecureObject"},
        },
        "triggers": build_trigger(),
        "actions": {
            CREATE_ACTION_KEY: build_create_decision_action(),
            RESPONSE_ACTION_KEY: build_respond_action(),
        },
    }


def build_connection_references(connection_reference_logical_name: str) -> dict:
    return {
        CONNECTOR_LOGICAL_NAME: {
            "api": {"name": CONNECTOR_LOGICAL_NAME},
            "connection": {
                "connectionReferenceLogicalName": connection_reference_logical_name
            },
            "runtimeSource": "embedded",
        }
    }


def build_clientdata(connection_reference_logical_name: str) -> dict:
    return {
        "properties": {
            "connectionReferences": build_connection_references(
                connection_reference_logical_name
            ),
            "definition": build_workflow_definition(),
        },
        "schemaVersion": SCHEMA_VERSION,
    }


def build_clientdata_string(connection_reference_logical_name: str) -> str:
    return json.dumps(
        build_clientdata(connection_reference_logical_name),
        ensure_ascii=False,
        separators=(",", ":"),
    )
