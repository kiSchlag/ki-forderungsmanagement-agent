"""Build scenario_table_shortlist.json from dataverse_metadata.json.

Pure local file analysis - streams the schema with ijson so the full file
never lives in memory at once. Reads the metadata dump produced by
``ki_forderungsmanagement.export_metadata`` and emits the curated shortlist
that drives ``ki_forderungsmanagement.provision_schema``.
"""

import json
import sys

import ijson

from .config import load_config


# ---------------------------------------------------------------------------
# Step 2 - canonical entities extracted from the 10 scenarios
# ---------------------------------------------------------------------------

EXTRACTED_ENTITIES = [
    "customer", "contact", "address", "invoice", "invoice_line", "payment",
    "sales_order", "sales_order_line", "quote", "product", "price_list",
    "email", "task", "case_or_dispute", "phone_call", "appointment",
    "letter", "note_or_attachment", "contract", "contract_line",
    "user", "team", "business_unit", "transaction_currency",
    "queue", "queue_item", "connection",
    "payment_event", "payment_plan", "sepa_mandate", "dunning_event",
    "credit_signal", "risk_score_history", "agent_decision_log",
    "delivery_note", "lifetime_value_snapshot", "churn_signal",
    "communication_preference",
]


# ---------------------------------------------------------------------------
# Step 3 - entity -> standard Dataverse table mapping
# Each entry pairs a canonical entity to the best-fit core CDM table and
# the scenarios where the entity appears (1-10).
# ---------------------------------------------------------------------------

STANDARD_MAP = [
    ("customer",                "account",            [1,2,3,4,5,6,7,8,9,10]),
    ("contact",                 "contact",            [2,3,8,10]),
    ("address",                 "customeraddress",    [3,5,6,9]),
    ("invoice",                 "invoice",            [1,3,4,5,6,7,9]),
    ("invoice_line",            "invoicedetail",      [6,9]),
    ("sales_order",             "salesorder",         [5,6,9]),
    ("sales_order_line",        "salesorderdetail",   [5,6,9]),
    ("quote",                   "quote",              [5,9]),
    ("product",                 "product",            [9]),
    ("price_list",              "pricelevel",         [9]),
    ("email",                   "email",              [2,3,4,8,10]),
    ("task",                    "task",               [3,6,8,10]),
    ("case_or_dispute",         "incident",           [3,6,8,10]),
    ("phone_call",              "phonecall",          [3,8,10]),
    ("appointment",             "appointment",        [3,8]),
    ("letter",                  "letter",             [2,4,10]),
    ("note_or_attachment",      "annotation",         [3,6,10]),
    ("contract",                "contract",           [9]),
    ("contract_line",           "contractdetail",     [9]),
    ("user",                    "systemuser",         [2,5,6,8,10]),
    ("team",                    "team",               [5,6,8,10]),
    ("business_unit",           "businessunit",       [7,10]),
    ("transaction_currency",    "transactioncurrency",[1,4,7]),
    ("queue",                   "queue",              [3]),
    ("queue_item",              "queueitem",          [3]),
    ("connection",              "connection",         [8]),
]


# ---------------------------------------------------------------------------
# Step 4 - custom tables that no standard CDM entity covers cleanly
# ---------------------------------------------------------------------------

CUSTOM_TABLES = [
    {
        "proposed_logical_name": "mueller_paymentevent",
        "purpose": "Records every payment receipt with date, amount, method, and the invoice it settles. Dataverse has no first-class payment table.",
        "scenarios_served": [1,4,5,7],
        "proposed_columns": [
            {"logical_name": "mueller_paymenteventid",   "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_name",             "type": "String",           "purpose": "Display name (auto-generated payment reference)"},
            {"logical_name": "mueller_invoiceid",        "type": "Lookup",           "purpose": "Lookup -> invoice the payment settles"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account (denormalized for fast reporting)"},
            {"logical_name": "mueller_paymentdate",      "type": "DateTime",         "purpose": "When the payment cleared"},
            {"logical_name": "mueller_amount",           "type": "Money",            "purpose": "Payment amount in customer currency"},
            {"logical_name": "mueller_currencyid",       "type": "Lookup",           "purpose": "Lookup -> transactioncurrency"},
            {"logical_name": "mueller_paymentmethod",    "type": "OptionSet",        "purpose": "SEPA / wire / card / check / cash"},
            {"logical_name": "mueller_bankreference",    "type": "String",           "purpose": "Bank reference / SEPA mandate ID"},
            {"logical_name": "mueller_dayslate",         "type": "Integer",          "purpose": "Days late vs invoice due date (0 = on time)"},
            {"logical_name": "mueller_exchangerate",     "type": "Decimal",          "purpose": "FX rate at time of payment for non-EUR currencies"},
            {"logical_name": "mueller_baseamount",       "type": "Money",            "purpose": "Payment amount converted to EUR base currency"},
        ],
    },
    {
        "proposed_logical_name": "mueller_paymentplan",
        "purpose": "Header for a multi-installment payment plan negotiated with a customer for an overdue balance.",
        "scenarios_served": [4,5,6],
        "proposed_columns": [
            {"logical_name": "mueller_paymentplanid",    "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_name",             "type": "String",           "purpose": "Plan reference (e.g. PP-2026-0042)"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_invoiceid",        "type": "Lookup",           "purpose": "Original invoice covered by the plan"},
            {"logical_name": "mueller_totalamount",      "type": "Money",            "purpose": "Total balance being rescheduled"},
            {"logical_name": "mueller_installmentcount", "type": "Integer",          "purpose": "Number of installments"},
            {"logical_name": "mueller_startdate",        "type": "DateTime",         "purpose": "First installment due date"},
            {"logical_name": "mueller_status",           "type": "OptionSet",        "purpose": "Proposed / Active / Completed / Defaulted"},
            {"logical_name": "mueller_acceptedby",       "type": "Lookup",           "purpose": "Lookup -> contact who accepted"},
            {"logical_name": "mueller_accepteddate",     "type": "DateTime",         "purpose": "When the customer accepted"},
        ],
    },
    {
        "proposed_logical_name": "mueller_paymentplaninstallment",
        "purpose": "One scheduled installment within a payment plan.",
        "scenarios_served": [4],
        "proposed_columns": [
            {"logical_name": "mueller_paymentplaninstallmentid", "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_paymentplanid",    "type": "Lookup",           "purpose": "Parent plan"},
            {"logical_name": "mueller_sequence",         "type": "Integer",          "purpose": "1, 2, 3 ..."},
            {"logical_name": "mueller_duedate",          "type": "DateTime",         "purpose": "Installment due date"},
            {"logical_name": "mueller_amount",           "type": "Money",            "purpose": "Amount due this installment"},
            {"logical_name": "mueller_status",           "type": "OptionSet",        "purpose": "Pending / Paid / Late / Missed"},
            {"logical_name": "mueller_paymenteventid",   "type": "Lookup",           "purpose": "Lookup -> mueller_paymentevent that settled this installment"},
        ],
    },
    {
        "proposed_logical_name": "mueller_sepamandate",
        "purpose": "SEPA direct-debit mandate per customer (mandate ID, signed date, IBAN-tail, status). DACH-specific. Stores full SEPA mandate per German DK / Bundesbank rules. IBAN is in scope here because demo data is synthetic; in production this would live in a vault.",
        "scenarios_served": [4],
        "proposed_columns": [
            {"logical_name": "mueller_sepamandateid",    "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_mandatereference", "type": "String",           "purpose": "Unique mandate reference per SEPA rules"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_ibantail",         "type": "String",           "purpose": "Last 4 digits of IBAN (do not store full IBAN here)"},
            {"logical_name": "mueller_signeddate",       "type": "DateTime",         "purpose": "When the mandate was signed by the customer"},
            {"logical_name": "mueller_status",           "type": "OptionSet",        "purpose": "Active / Suspended / Revoked / Expired"},
            {"logical_name": "mueller_lastuseddate",     "type": "DateTime",         "purpose": "Last successful use (for 36-month dormancy rule)"},
            {"logical_name": "mueller_iban",             "type": "String",           "max_length": 34, "purpose": "Full IBAN (synthetic demo data only - real systems vault this)"},
            {"logical_name": "mueller_bic",              "type": "String",           "max_length": 11, "purpose": "BIC / SWIFT code"},
            {"logical_name": "mueller_creditorid",       "type": "String",           "max_length": 35, "purpose": "German Glaeubiger-ID, e.g. DE98ZZZ09999999999"},
            {"logical_name": "mueller_mandatetype",      "type": "OptionSet",        "purpose": "CORE / B2B / COR1"},
            {"logical_name": "mueller_firstuseflag",     "type": "Boolean",          "purpose": "True if SEPA pre-notification still required for the next collection"},
            {"logical_name": "mueller_amendmentcount",   "type": "Integer",          "purpose": "Tracks mandate amendments per SEPA rules"},
        ],
    },
    {
        "proposed_logical_name": "mueller_dunningevent",
        "purpose": "Each dunning step taken on an invoice (gentle reminder, formal demand, escalation, legal). Letter/email activity tables don't capture the dunning workflow state.",
        "scenarios_served": [2,4,8,10],
        "proposed_columns": [
            {"logical_name": "mueller_dunningeventid",   "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_invoiceid",        "type": "Lookup",           "purpose": "Lookup -> invoice"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account (denormalized)"},
            {"logical_name": "mueller_stage",            "type": "OptionSet",        "purpose": "Friendly / Standard / Escalated / FinalDemand / Legal"},
            {"logical_name": "mueller_tone",             "type": "OptionSet",        "purpose": "Friendly / Standard / Strict / WhiteGlove"},
            {"logical_name": "mueller_channel",          "type": "OptionSet",        "purpose": "Email / Letter / PhoneCall"},
            {"logical_name": "mueller_sentdate",         "type": "DateTime",         "purpose": "When the dunning was sent"},
            {"logical_name": "mueller_emailid",          "type": "Lookup",           "purpose": "Lookup -> email activity"},
            {"logical_name": "mueller_letterid",         "type": "Lookup",           "purpose": "Lookup -> letter activity"},
            {"logical_name": "mueller_responsereceived", "type": "Boolean",          "purpose": "Did customer respond?"},
        ],
    },
    {
        "proposed_logical_name": "mueller_creditsignal",
        "purpose": "External credit-bureau signal (e.g. Creditreform downgrade, insolvency notice). Inputs to the daily risk score.",
        "scenarios_served": [1,5],
        "proposed_columns": [
            {"logical_name": "mueller_creditsignalid",   "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_source",           "type": "OptionSet",        "purpose": "Creditreform / Bisnode / SCHUFA / Manual / News"},
            {"logical_name": "mueller_signaltype",       "type": "OptionSet",        "purpose": "ScoreChange / Insolvency / LegalAction / NewsAlert / OwnerChange"},
            {"logical_name": "mueller_severity",         "type": "OptionSet",        "purpose": "Info / Warning / Critical"},
            {"logical_name": "mueller_score",            "type": "Decimal",          "purpose": "Numeric score from source if applicable"},
            {"logical_name": "mueller_receiveddate",     "type": "DateTime",         "purpose": "When the signal was received"},
            {"logical_name": "mueller_payload",          "type": "Memo",             "purpose": "Raw signal payload for auditing"},
        ],
    },
    {
        "proposed_logical_name": "mueller_riskscoresnapshot",
        "purpose": "Daily risk score per open invoice. Time-series so we can see baseline drift per customer over time.",
        "scenarios_served": [1,2,7,8],
        "proposed_columns": [
            {"logical_name": "mueller_riskscoresnapshotid", "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_invoiceid",        "type": "Lookup",           "purpose": "Lookup -> invoice"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account (denormalized)"},
            {"logical_name": "mueller_snapshotdate",     "type": "DateTime",         "purpose": "Date the score was computed (daily)"},
            {"logical_name": "mueller_riskscore",        "type": "Decimal",          "purpose": "0.0 - 1.0 probability of late payment"},
            {"logical_name": "mueller_modelversion",     "type": "String",           "purpose": "Which model produced this score"},
            {"logical_name": "mueller_topfeatures",      "type": "Memo",             "purpose": "JSON: top contributing features for explainability"},
            {"logical_name": "mueller_thresholdcrossed", "type": "Boolean",          "purpose": "Did this snapshot cross the high-risk threshold today?"},
        ],
    },
    {
        "proposed_logical_name": "mueller_agentdecision",
        "purpose": "Every action the agent takes - decision, reasoning, inputs used, outcome. The audit-trail backbone for scenario 10.",
        "scenarios_served": [1,2,3,4,5,6,7,8,9,10],
        "proposed_columns": [
            {"logical_name": "mueller_agentdecisionid",  "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_scenario",         "type": "OptionSet",        "purpose": "Which of the 10 scenarios this decision belongs to"},
            {"logical_name": "mueller_decisiontype",     "type": "OptionSet",        "purpose": "RiskScored / EmailSent / OrderHeld / DisputeRouted / PlanProposed / etc."},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_invoiceid",        "type": "Lookup",           "purpose": "Lookup -> invoice (when applicable)"},
            {"logical_name": "mueller_orderid",          "type": "Lookup",           "purpose": "Lookup -> salesorder (when applicable)"},
            {"logical_name": "mueller_decidedat",        "type": "DateTime",         "purpose": "Timestamp"},
            {"logical_name": "mueller_reasoning",        "type": "Memo",             "purpose": "Plain-language reasoning the agent recorded"},
            {"logical_name": "mueller_inputshash",       "type": "String",           "purpose": "Hash of the input data used (for reproducibility)"},
            {"logical_name": "mueller_inputspayload",    "type": "Memo",             "purpose": "JSON snapshot of inputs used in the decision"},
            {"logical_name": "mueller_outcome",          "type": "OptionSet",        "purpose": "Executed / Suppressed / RoutedToHuman / Overridden"},
            {"logical_name": "mueller_overrideby",       "type": "Lookup",           "purpose": "Lookup -> systemuser if a human overrode"},
            {"logical_name": "mueller_overridereason",   "type": "Memo",             "purpose": "Why the human overrode (training signal)"},
            {"logical_name": "mueller_confidencescore",  "type": "Decimal",          "purpose": "Model confidence 0.0-1.0; low values are auto-routed to humans"},
            {"logical_name": "mueller_modelname",        "type": "String",           "purpose": "Which model produced the decision (gpt-4o, custom-classifier-v2, etc.)"},
            {"logical_name": "mueller_promptversion",    "type": "String",           "purpose": "Prompt template version for reproducibility"},
        ],
    },
    {
        "proposed_logical_name": "mueller_deliverynote",
        "purpose": "Hardware delivery note with line items linked to a sales order. Needed for proof-of-delivery dossiers in dispute handling.",
        "scenarios_served": [3,6],
        "proposed_columns": [
            {"logical_name": "mueller_deliverynoteid",   "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_name",             "type": "String",           "purpose": "Delivery note number"},
            {"logical_name": "mueller_orderid",          "type": "Lookup",           "purpose": "Lookup -> salesorder"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_deliverydate",     "type": "DateTime",         "purpose": "When goods were delivered"},
            {"logical_name": "mueller_carrier",          "type": "String",           "purpose": "Shipping carrier"},
            {"logical_name": "mueller_trackingnumber",   "type": "String",           "purpose": "Carrier tracking number"},
            {"logical_name": "mueller_signedbyname",     "type": "String",           "purpose": "Recipient signature name"},
            {"logical_name": "mueller_signedimageurl",   "type": "String",           "purpose": "SharePoint URL of signed POD image"},
        ],
    },
    {
        "proposed_logical_name": "mueller_lifetimevaluesnapshot",
        "purpose": "Periodic LTV calculation per customer with components (revenue, margin, tenure). Used to choose tone of communication.",
        "scenarios_served": [2,8],
        "proposed_columns": [
            {"logical_name": "mueller_lifetimevaluesnapshotid", "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_snapshotdate",     "type": "DateTime",         "purpose": "When LTV was calculated"},
            {"logical_name": "mueller_ltvamount",        "type": "Money",            "purpose": "Lifetime value in EUR"},
            {"logical_name": "mueller_revenuetotal",     "type": "Money",            "purpose": "Cumulative revenue to date"},
            {"logical_name": "mueller_margintotal",      "type": "Money",            "purpose": "Cumulative margin"},
            {"logical_name": "mueller_tenuredays",       "type": "Integer",          "purpose": "Days as a customer"},
            {"logical_name": "mueller_segment",          "type": "OptionSet",        "purpose": "Strategic / Loyal / MidTier / NewOrAtRisk"},
        ],
    },
    {
        "proposed_logical_name": "mueller_churnsignal",
        "purpose": "Detected early churn signal (order frequency drop, complaint spike, sentiment decline) with severity.",
        "scenarios_served": [8],
        "proposed_columns": [
            {"logical_name": "mueller_churnsignalid",    "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_signaltype",       "type": "OptionSet",        "purpose": "OrderFrequencyDrop / ComplaintSpike / NegativeSentiment / Disengagement"},
            {"logical_name": "mueller_severity",         "type": "OptionSet",        "purpose": "Low / Medium / High"},
            {"logical_name": "mueller_detecteddate",     "type": "DateTime",         "purpose": "When the signal fired"},
            {"logical_name": "mueller_baselinevalue",    "type": "Decimal",          "purpose": "What 'normal' looked like"},
            {"logical_name": "mueller_currentvalue",     "type": "Decimal",          "purpose": "What was observed"},
            {"logical_name": "mueller_routedtouserid",   "type": "Lookup",           "purpose": "Account manager assigned"},
        ],
    },
    {
        "proposed_logical_name": "mueller_communicationpreference",
        "purpose": "Per-customer preferences (formal Sie vs informal Du, channel, suppress automated dunning, language).",
        "scenarios_served": [2,8,10],
        "proposed_columns": [
            {"logical_name": "mueller_communicationpreferenceid", "type": "Uniqueidentifier", "purpose": "Primary key"},
            {"logical_name": "mueller_customerid",       "type": "Lookup",           "purpose": "Lookup -> account"},
            {"logical_name": "mueller_formality",        "type": "OptionSet",        "purpose": "Sie / Du"},
            {"logical_name": "mueller_preferredchannel", "type": "OptionSet",        "purpose": "Email / Letter / Phone / Portal"},
            {"logical_name": "mueller_suppressautomation","type": "Boolean",         "purpose": "Always route to a human"},
            {"logical_name": "mueller_language",         "type": "String",           "purpose": "BCP-47 e.g. de-DE, de-AT, de-CH"},
            {"logical_name": "mueller_quiethoursstart",  "type": "Integer",          "purpose": "Hour of day to suppress sends"},
            {"logical_name": "mueller_quiethoursend",    "type": "Integer",          "purpose": "Hour of day to resume"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Step 4 - custom columns to add to existing standard tables
# ---------------------------------------------------------------------------

CUSTOM_COLUMNS = [
    {"target_table": "account", "new_column_logical_name": "mueller_paymentriskscore",   "type": "Decimal",   "purpose": "Daily aggregate risk score 0-1 across the customer's open invoices",                                "scenarios_served": [1,2,5,7]},
    {"target_table": "account", "new_column_logical_name": "mueller_segment",            "type": "OptionSet", "purpose": "Strategic / Loyal / MidTier / RepeatLatePayer / New - drives tone of communication",                "scenarios_served": [2,8]},
    {"target_table": "account", "new_column_logical_name": "mueller_communicationtone",  "type": "OptionSet", "purpose": "Friendly / Standard / Strict / WhiteGlove - precomputed tone for outbound dunning",                "scenarios_served": [2]},
    {"target_table": "account", "new_column_logical_name": "mueller_lifetimevalue",      "type": "Money",     "purpose": "Latest snapshot of LTV (denormalized from mueller_lifetimevaluesnapshot for fast filtering). Refresh policy: updated daily by a background job that reads from mueller_lifetimevaluesnapshot. The snapshot table is the source of truth; this column is a denormalized convenience for fast filtering and dashboards.", "scenarios_served": [2,8]},
    {"target_table": "account", "new_column_logical_name": "mueller_dsodays",            "type": "Decimal",   "purpose": "Customer-level DSO over rolling window",                                                            "scenarios_served": [1,7]},
    {"target_table": "account", "new_column_logical_name": "mueller_creditreformscore",  "type": "Decimal",   "purpose": "Latest external credit-bureau score (Creditreform / Bisnode)",                                      "scenarios_served": [1,5]},
    {"target_table": "account", "new_column_logical_name": "mueller_creditreformupdated","type": "DateTime",  "purpose": "When the external credit score was last refreshed",                                                 "scenarios_served": [1,5]},
    {"target_table": "account", "new_column_logical_name": "mueller_isstrategicaccount", "type": "Boolean",   "purpose": "Marks accounts that always require human-in-the-loop handling",                                     "scenarios_served": [2,5,8]},
    {"target_table": "account", "new_column_logical_name": "mueller_keyaccountmanager",  "type": "Lookup",    "purpose": "Lookup -> systemuser - the human owner for white-glove handling",                                   "scenarios_served": [2,5,8]},
    {"target_table": "account", "new_column_logical_name": "mueller_blockorders",        "type": "Boolean",   "purpose": "Hard block on accepting new orders (advanced collection / insolvency)",                             "scenarios_served": [5]},

    {"target_table": "invoice", "new_column_logical_name": "mueller_riskscore",          "type": "Decimal",   "purpose": "Latest predictive late-payment risk score 0-1 for this invoice",                                    "scenarios_served": [1,7]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_riskscoreupdated",   "type": "DateTime",  "purpose": "When mueller_riskscore was last computed",                                                          "scenarios_served": [1]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_isdisputed",         "type": "Boolean",   "purpose": "Customer has disputed this invoice (set from email classification)",                                "scenarios_served": [3,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_disputereason",      "type": "Memo",      "purpose": "Free-text dispute reason captured from inbound email",                                              "scenarios_served": [3,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_paymentplanid",      "type": "Lookup",    "purpose": "Lookup -> mueller_paymentplan - non-null if this invoice is in a rescheduled plan",                "scenarios_served": [4]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_dunningstage",       "type": "OptionSet", "purpose": "Current dunning stage on the invoice (last mueller_dunningevent denormalized)",                     "scenarios_served": [2,4]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_extensiongranted",   "type": "DateTime",  "purpose": "New due date if an extension was granted (null otherwise)",                                         "scenarios_served": [4]},

    {"target_table": "salesorder", "new_column_logical_name": "mueller_oncreditholdreason", "type": "OptionSet", "purpose": "Why the order is on credit hold (BalanceExceeded / InsolvencySignal / Manual)",                  "scenarios_served": [5]},
    {"target_table": "salesorder", "new_column_logical_name": "mueller_holdplaceddate",     "type": "DateTime",  "purpose": "When the order was placed on hold",                                                              "scenarios_served": [5]},
    {"target_table": "salesorder", "new_column_logical_name": "mueller_releasedate",        "type": "DateTime",  "purpose": "When the order was released from hold (null = still held)",                                      "scenarios_served": [5]},
    {"target_table": "salesorder", "new_column_logical_name": "mueller_holdoverrideby",     "type": "Lookup",    "purpose": "Lookup -> systemuser - the rep who overrode the hold for relationship-critical cases",          "scenarios_served": [5]},

    {"target_table": "email", "new_column_logical_name": "mueller_classification",      "type": "OptionSet", "purpose": "Reschedule / Dispute / ProofOfDelivery / PaymentConfirmation / GeneralQuestion",                    "scenarios_served": [3]},
    {"target_table": "email", "new_column_logical_name": "mueller_languagedetected",    "type": "String",    "purpose": "BCP-47 language tag detected from message body",                                                    "scenarios_served": [3,10]},
    {"target_table": "email", "new_column_logical_name": "mueller_sentimentscore",      "type": "Decimal",   "purpose": "-1 to 1 sentiment score - negative values trigger escalation routing",                              "scenarios_served": [3,8]},
    {"target_table": "email", "new_column_logical_name": "mueller_routedtouserid",      "type": "Lookup",    "purpose": "Lookup -> systemuser the email was routed to (escalation, complex case)",                          "scenarios_served": [3,8]},
    {"target_table": "email", "new_column_logical_name": "mueller_agentdrafted",        "type": "Boolean",   "purpose": "True if the agent generated this email draft (vs. fully autonomous send)",                          "scenarios_served": [2,3,10]},

    {"target_table": "incident", "new_column_logical_name": "mueller_disputetype",      "type": "OptionSet", "purpose": "PriceMismatch / QuantityShort / DamagedGoods / WrongItem / TaxIncorrect / NotReceived",             "scenarios_served": [6]},
    {"target_table": "incident", "new_column_logical_name": "mueller_invoiceid",        "type": "Lookup",    "purpose": "Lookup -> invoice the dispute is about",                                                            "scenarios_served": [6]},
    {"target_table": "incident", "new_column_logical_name": "mueller_resolutiondays",   "type": "Integer",   "purpose": "Days from creation to resolution (for stall detection)",                                            "scenarios_served": [6]},

    {"target_table": "contract", "new_column_logical_name": "mueller_renewaltriggerdate","type": "DateTime", "purpose": "Date 90 days before contract end - triggers renewal workflow",                                       "scenarios_served": [9]},
    {"target_table": "contract", "new_column_logical_name": "mueller_billingrhythm",     "type": "OptionSet","purpose": "Annual / Quarterly / Monthly / Milestone / OnDelivery",                                              "scenarios_served": [9]},
    {"target_table": "contract", "new_column_logical_name": "mueller_autorenewenabled",  "type": "Boolean",  "purpose": "Should renewal happen automatically when no opt-out is received?",                                   "scenarios_served": [9]},

    # German USt-Steuer fields on invoice header
    {"target_table": "invoice", "new_column_logical_name": "mueller_taxrate",         "type": "Decimal",   "purpose": "German USt-Satz 19.0 / 7.0 / 0.0",                                                                       "scenarios_served": [6,9]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_taxamount",       "type": "Money",     "purpose": "Calculated USt-Betrag for the invoice",                                                                  "scenarios_served": [6,9]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_netamount",       "type": "Money",     "purpose": "Nettobetrag (pre-tax)",                                                                                   "scenarios_served": [6,9]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_grossamount",     "type": "Money",     "purpose": "Bruttobetrag (post-tax)",                                                                                 "scenarios_served": [6,9]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_reversecharge",   "type": "Boolean",   "purpose": "Intra-EU reverse charge flag (Section 13b UStG)",                                                          "scenarios_served": [6,9]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_buyervatid",      "type": "String",    "purpose": "Customer USt-IdNr. captured on the invoice (required for B2B intra-EU)",                                  "scenarios_served": [6,9]},

    # German USt fields per invoice line
    {"target_table": "invoicedetail", "new_column_logical_name": "mueller_linetaxrate",   "type": "Decimal", "purpose": "USt-Satz applied to this invoice line",                                                                "scenarios_served": [6,9]},
    {"target_table": "invoicedetail", "new_column_logical_name": "mueller_linetaxamount", "type": "Money",   "purpose": "USt-Betrag for this invoice line",                                                                     "scenarios_served": [6,9]},
    {"target_table": "invoicedetail", "new_column_logical_name": "mueller_linenetamount", "type": "Money",   "purpose": "Nettobetrag for this invoice line",                                                                    "scenarios_served": [6,9]},

    # German legal-identity fields on the customer (account)
    {"target_table": "account", "new_column_logical_name": "mueller_legalform",                "type": "OptionSet", "purpose": "GmbH / AG / KG / OHG / GbR / UG / Einzelunternehmen / Sonstige",                                "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_handelsregisternummer",    "type": "String",    "purpose": "HRB or HRA number (max 20)",                                                                    "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_amtsgericht",              "type": "String",    "purpose": "Registry court that holds the Handelsregister entry (max 80)",                                  "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_ustidnr",                  "type": "String",    "purpose": "USt-IdNr., format DE + 9 digits (max 20)",                                                       "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_steuernummer",             "type": "String",    "purpose": "Finanzamt Steuernummer (max 20)",                                                               "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_glaeubigerid",             "type": "String",    "purpose": "Customer's own Glaeubiger-ID if relevant (max 35)",                                              "scenarios_served": [2,5,6,9,10]},
    {"target_table": "account", "new_column_logical_name": "mueller_kontaktpreferenz_anrede",  "type": "OptionSet", "purpose": "Herr / Frau / Divers / Firma - drives salutation in dunning letters",                            "scenarios_served": [2,5,6,9,10]},

    # Open-balance denormalization on the customer (refreshed daily by background job)
    {"target_table": "account", "new_column_logical_name": "mueller_openbalance",          "type": "Money",    "purpose": "Sum of outstanding amounts across all open invoices for this customer. Refreshed daily by background job.",       "scenarios_served": [1,5,7]},
    {"target_table": "account", "new_column_logical_name": "mueller_overduebalance",       "type": "Money",    "purpose": "Subset of mueller_openbalance that is past due. Refreshed daily by background job.",                              "scenarios_served": [1,5,7]},
    {"target_table": "account", "new_column_logical_name": "mueller_oldestoverduedate",    "type": "DateTime", "purpose": "Due date of the oldest unpaid invoice; null if no overdue. Refreshed daily by background job.",                    "scenarios_served": [1,5,7]},
    {"target_table": "account", "new_column_logical_name": "mueller_balancelastrefreshed", "type": "DateTime", "purpose": "Timestamp of the last balance recomputation",                                                                       "scenarios_served": [1,5,7]},

    # Per-invoice paid / outstanding tracking (sums of related mueller_paymentevent)
    {"target_table": "invoice", "new_column_logical_name": "mueller_paidamount",         "type": "Money",   "purpose": "Cumulative amount paid against this invoice (sum of related mueller_paymentevent.mueller_amount)",                  "scenarios_served": [1,4,5,6,7]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_outstandingamount",  "type": "Money",   "purpose": "Remaining unpaid amount; if zero, invoice is fully settled",                                                       "scenarios_served": [1,4,5,6,7]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_partiallypaid",      "type": "Boolean", "purpose": "True if mueller_paidamount > 0 and mueller_outstandingamount > 0",                                                  "scenarios_served": [1,4,5,6,7]},

    # German Skonto (early-payment discount) modeling on invoice
    {"target_table": "invoice", "new_column_logical_name": "mueller_skontopercent",      "type": "Decimal", "purpose": "Early payment discount percentage, e.g. 2.0 means 2% off",                                                          "scenarios_served": [1,2,4,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_skontodeadline",     "type": "DateTime","purpose": "Last date Skonto can be claimed",                                                                                   "scenarios_served": [1,2,4,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_skontoamount",       "type": "Money",   "purpose": "Calculated Skonto amount in EUR",                                                                                   "scenarios_served": [1,2,4,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_skontoclaimed",      "type": "Boolean", "purpose": "Did the customer actually pay within the Skonto window?",                                                          "scenarios_served": [1,2,4,6]},
    {"target_table": "invoice", "new_column_logical_name": "mueller_nettodeadline",      "type": "DateTime","purpose": "Standard non-Skonto due date (same value as duedate but stored explicitly for clarity in payment-terms reasoning)", "scenarios_served": [1,2,4,6]},
]


# ---------------------------------------------------------------------------
# Column filtering helpers
# ---------------------------------------------------------------------------

# Standard system fields that should always be included on every table.
TIER1_EXACT = {
    "createdon", "modifiedon", "createdby", "modifiedby",
    "statecode", "statuscode", "ownerid", "transactioncurrencyid",
}

# Truly business-critical AR/finance fields - these get the top scoring
# tier so they always beat address slots when slots are tight.
ESSENTIAL_AR_SUBSTRINGS = (
    # Payment / credit / collections specifics
    "creditlimit", "creditonhold", "paymentterm", "duedate", "balance",
    "lastonholdtime", "onholdtime",
    # Identity & numbers
    "accountnumber", "ordernumber", "invoicenumber", "quotenumber",
    "ticketnumber", "contractnumber",
    # Money totals
    "totalamount", "totaltax", "freightamount", "totaldiscount",
    "totallineitem", "extendedamount", "priceperunit",
    # Contract / billing dates
    "billingstartdate", "billingenddate", "validfrom", "validto",
    "expirationdate", "activeon", "activeoff", "renewaldate",
    "shipdate", "actualstart", "actualend", "datefulfilled",
    "datedelivered", "dateinvoiced", "submitdate",
    # Customer / activity attributes that drive AR logic
    "customertypecode", "customersizecode", "accountcategorycode",
    "industrycode", "prioritycode", "directioncode",
)

# Important relational lookups - second tier.
RELATIONAL_LOOKUPS = (
    "customerid", "primarycontactid", "parentaccountid", "parentcustomerid",
    "accountid", "contactid", "regardingobjectid", "salesorderid",
    "invoiceid", "quoteid", "productid", "subjectid",
    "sender", "torecipients",
)

# Communication channel basics - third tier.
COMMUNICATION_BASICS = (
    "emailaddress1", "telephone1", "mobilephone", "fullname",
    "firstname", "lastname", "fax", "preferredcontactmethodcode",
    "subject",
)

# Lower-value generic keywords (count of matches becomes the score).
GENERIC_KEYWORDS = {
    "payment", "invoice", "due", "balance", "credit", "currency",
    "email", "phone", "name", "status", "amount", "total",
    "discount", "tax", "subject", "description",
    "deliver", "ship", "renew", "expire", "billing",
}

# Pure system plumbing - always exclude even if keyword matches.
SYSTEM_PLUMBING = {
    "importsequencenumber", "overriddencreatedon", "timezoneruleversionnumber",
    "utcconversiontimezonecode", "versionnumber", "traversedpath",
    "processid", "stageid", "azureactivedirectoryobjectid",
    "haveprivilegestoshare", "exchangerate", "owningbusinessunit",
    "owninguser", "owningteam", "createdonbehalfby", "modifiedonbehalfby",
    "emailsender", "sendermailboxid", "deliveryattempts",
    "address1_addresstypecode", "address1_addressid",
    "address1_freighttermscode", "address1_shippingmethodcode",
    "address1_upszone", "address1_utcoffset", "address1_county",
    "address1_longitude", "address1_latitude",
}

# Substring patterns that indicate noisy mirror / duplicate / internal fields.
EXCLUDE_SUBSTRINGS = (
    "_base",          # Money base-currency mirrors
    "codename",       # OptionSet name mirrors (e.g. paymenttermscodename)
    "yominame",       # Japanese phonetic mirrors
    "address2_",      # Secondary address slots
    "address3_",      # Tertiary address slots
    "_composite",     # Concatenated address mirrors (billto_composite, etc.)
    "correlated",     # Email correlated-subject noise
    "_addressid",     # Address record FK noise
)

# Address-slot whitelist: anything starting with address1_/billto_/shipto_
# is dropped UNLESS it appears here. Stops bill-to / address slots from
# crowding out actual AR fields like duedate / creditlimit.
ALLOWED_ADDRESS_FIELDS = {
    "address1_city", "address1_country", "address1_postalcode",
    "address1_stateorprovince", "address1_line1", "address1_telephone1",
    "billto_city", "billto_country", "billto_postalcode", "billto_stateorprovince",
    "shipto_city", "shipto_country", "shipto_postalcode",
}

ADDRESS_PREFIXES = ("address1_", "billto_", "shipto_")


def column_score(col: dict, primary_id: str, primary_name: str) -> int:
    """Higher score = more relevant. -1 means exclude entirely.

    Tier ordering, top to bottom:
        primary key / primary name  (10000)
        AR business-critical names  (5000) - duedate, creditlimit, totals, etc.
        statecode / statuscode      (3000)
        createdon / modifiedon / ownerid (2000)
        createdby / modifiedby / transactioncurrencyid (1000)
        generic keyword count fallback (1-N)
    """
    name = (col.get("logical_name") or "").lower()
    if not name:
        return -1
    if name in SYSTEM_PLUMBING:
        return -1
    if name.endswith("idname"):  # lookup display-name mirrors
        return -1
    if any(sub in name for sub in EXCLUDE_SUBSTRINGS):
        return -1
    # Drop address-slot variants that are not in our allowlist.
    if name.startswith(ADDRESS_PREFIXES) and name not in ALLOWED_ADDRESS_FIELDS:
        return -1
    if name == primary_id or name == primary_name:
        return 10000
    if any(sub in name for sub in ESSENTIAL_AR_SUBSTRINGS):
        return 7000
    if any(sub in name for sub in RELATIONAL_LOOKUPS):
        return 5000
    if any(sub in name for sub in COMMUNICATION_BASICS):
        return 4000
    if name in ALLOWED_ADDRESS_FIELDS:
        return 3000
    if name in ("statecode", "statuscode"):
        return 2500
    if name in ("createdon", "modifiedon", "ownerid"):
        return 2000
    if name in ("createdby", "modifiedby", "transactioncurrencyid"):
        return 1000
    blob = " ".join(filter(None, [
        name,
        (col.get("display_name") or "").lower(),
        (col.get("description") or "").lower(),
    ]))
    score = sum(1 for kw in GENERIC_KEYWORDS if kw in blob)
    return score if score > 0 else -1


# Columns that must always appear when present on the table.
ALWAYS_INCLUDE_IF_PRESENT = (
    "statecode", "statuscode", "ownerid", "createdon", "modifiedon",
)

# Per-table verbatim column lists - bypasses scoring and address filters.
# Use when a hand-curated subset is required (e.g. customeraddress where
# the schema's "line1"/"city" don't match address1_* prefixes).
TABLE_COLUMN_OVERRIDES = {
    "customeraddress": [
        "customeraddressid", "name", "addressnumber", "addresstypecode",
        "line1", "line2", "city", "postalcode", "stateorprovince", "country",
        "telephone1", "parentid", "createdon", "modifiedon",
    ],
}


def filter_columns(table: dict, max_cols: int = 14) -> list:
    """Return up to max_cols most relevant columns for AR/finance use.

    Strategy: hand-curated override if present, else hard-include the
    universal must-haves (PK, primary name, statecode, statuscode, ownerid,
    createdon, modifiedon) and fill remaining slots by score.
    """
    ln_table = (table.get("logical_name") or "").lower()
    cols = table.get("columns", [])
    by_name = {(c.get("logical_name") or "").lower(): c for c in cols}

    if ln_table in TABLE_COLUMN_OVERRIDES:
        wanted = TABLE_COLUMN_OVERRIDES[ln_table]
        return [_slim_column(by_name[n]) for n in wanted if n in by_name]

    primary_id = (table.get("primary_id_attribute") or "").lower()
    primary_name = (table.get("primary_name_attribute") or "").lower()

    chosen: list = []
    chosen_names: set = set()

    def _take(name: str) -> None:
        if name and name in by_name and name not in chosen_names:
            chosen.append(by_name[name])
            chosen_names.add(name)

    _take(primary_id)
    _take(primary_name)
    for n in ALWAYS_INCLUDE_IF_PRESENT:
        _take(n)

    # Score the rest, fill remaining slots
    scored = []
    for col in cols:
        ln = (col.get("logical_name") or "").lower()
        if ln in chosen_names:
            continue
        # Drop adx_* portal extension noise; keep msdyn_ (first-party D365).
        if ln.startswith("adx_") or ln.startswith("mspp_"):
            continue
        s = column_score(col, primary_id, primary_name)
        if s > 0:
            scored.append((s, col))
    scored.sort(key=lambda x: (-x[0], x[1].get("logical_name") or ""))
    for _, c in scored[: max_cols - len(chosen)]:
        chosen.append(c)

    return [_slim_column(c) for c in chosen]


def _slim_column(col: dict) -> dict:
    out = {
        "logical_name": col.get("logical_name"),
        "schema_name": col.get("schema_name"),
        "display_name": col.get("display_name"),
        "type": col.get("type"),
        "required_level": col.get("required_level"),
    }
    desc = col.get("description")
    if desc:
        out["description"] = desc
    if col.get("max_length") is not None:
        out["max_length"] = col["max_length"]
    if col.get("format"):
        out["format"] = col["format"]
    if col.get("lookup_targets"):
        out["lookup_targets"] = col["lookup_targets"]
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def is_deprecated(display_name: str) -> bool:
    if not display_name:
        return False
    low = display_name.lower()
    return "deprecated" in low or "[deprecated]" in low


def main() -> None:
    _, paths, _ = load_config()
    source = paths.metadata_file
    output = paths.shortlist_file

    if not source.is_file():
        sys.exit(
            f"Metadata file not found: {source}\n"
            f"Run `python -m ki_forderungsmanagement.export_metadata` first."
        )

    needed = {table for _, table, _ in STANDARD_MAP}

    # Pass 1 - stream and index just the table-level metadata for the ones we want.
    print("Pass 1: streaming schema, indexing target tables...", file=sys.stderr)
    chosen: dict = {}
    seen = 0
    with source.open("rb") as fh:
        for table in ijson.items(fh, "tables.item"):
            seen += 1
            ln = table.get("logical_name")
            if ln in needed and ln not in chosen:
                if is_deprecated(table.get("display_name") or ""):
                    print(f"  ! skipping deprecated: {ln}", file=sys.stderr)
                    continue
                chosen[ln] = {
                    "logical_name": ln,
                    "schema_name": table.get("schema_name"),
                    "display_name": table.get("display_name"),
                    "description": table.get("description"),
                    "primary_id_attribute": table.get("primary_id_attribute"),
                    "primary_name_attribute": table.get("primary_name_attribute"),
                    "is_custom_entity": False,  # all targets are core CDM
                    "columns": filter_columns(table),
                }
    print(f"  scanned {seen} tables, matched {len(chosen)}/{len(needed)}", file=sys.stderr)

    missing = needed - set(chosen.keys())
    if missing:
        print(f"  ! NOT FOUND in schema: {sorted(missing)}", file=sys.stderr)

    # Build standard_tables list in mapping order
    standard_tables = []
    for entity, table_name, scenarios in STANDARD_MAP:
        if table_name in chosen:
            standard_tables.append({
                "entity_concept": entity,
                "scenarios_served": scenarios,
                "table": chosen[table_name],
            })
        else:
            standard_tables.append({
                "entity_concept": entity,
                "scenarios_served": scenarios,
                "table": None,
                "note": f"Logical name '{table_name}' not present in this environment",
            })

    document = {
        "extracted_entities": EXTRACTED_ENTITIES,
        "standard_tables": standard_tables,
        "custom_tables_needed": CUSTOM_TABLES,
        "custom_columns_to_add": CUSTOM_COLUMNS,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

    size = output.stat().st_size
    line_count = output.read_text(encoding="utf-8").count("\n") + 1

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total entities extracted:      {len(EXTRACTED_ENTITIES)}")
    print(f"Total standard tables:         {sum(1 for s in standard_tables if s['table'])}")
    print(f"Standard table lookups missing:{sum(1 for s in standard_tables if not s['table'])}")
    print(f"Total custom tables proposed:  {len(CUSTOM_TABLES)}")
    print(f"Total custom columns proposed: {len(CUSTOM_COLUMNS)}")
    print(f"Output file:                   {output}")
    print(f"Output size:                   {size:,} bytes ({size/1024:.1f} KB)")
    print(f"Output line count:             {line_count:,}")


if __name__ == "__main__":
    main()
