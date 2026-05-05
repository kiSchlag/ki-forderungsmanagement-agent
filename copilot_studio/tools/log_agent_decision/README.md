# Werkzeug: Log Agent Decision

> **Status**: in Entwicklung. Programmatisch über die Dataverse Web API
> bereitgestellt — kein Klick im Copilot-Studio-Maker erforderlich.
>
> **Komponenten dieses Verzeichnisses**:
> - [`README.md`](README.md) — diese Dokumentation (alles, was der Code nicht selbst erklärt)
> - [`flow_definition.py`](flow_definition.py) — erzeugt das Logic-Apps-Workflow-Definition-JSON
> - [`deploy.py`](deploy.py) — `POST /api/data/v9.2/workflows`, fügt zur Lösung hinzu, optional Aktivierung
>
> **Konvention**: Beide Python-Skripte enthalten **bewusst keine Kommentare und keine Docstrings**.
> Funktionsnamen tragen die Bedeutung; alles weitere steht hier in der README.

---

## Zweck

Ein Schreib-Werkzeug, das jede Entscheidung des KI-Agents als Datensatz in die
append-only-Tabelle `mueller_agentdecision` protokolliert. Genau ein
Datensatz pro Aufruf — Kunden-Bezug, Begründung, Konfidenzwert, Modellname und
optional Rechnungs-/Auftragsbezug.

Ohne dieses Werkzeug kann **kein anderes Schreib-Werkzeug** des Agents
verantwortungsvoll laufen, denn die Audit-Pflicht
([Instruktion I15](../../../docs/instruktionen.md)) verlangt für **jede**
sinnvolle Aktion eine Spur. T17 ist daher das Fundament, nicht die Kür.

## Geschäftsbezug

Direkter Bezug zu:

- **Szenario 10 — Compliance und Audit-Trail** (siehe
  [`docs/szenarien.md`](../../../docs/szenarien.md)). Konkret: jede
  Agent-Aktion mit Begründung, Datenquellen und Modellangabe protokollieren,
  GDPR-konformer Audit-Trail pro Kunde, menschliche Override-Möglichkeit.
- **Instruktion I15**: *„Protokolliere jede sinnvolle Aktion in
  `mueller_agentdecision` mit Begründung, Konfidenzwert und Modellname."*
- **Instruktion I16**: *„Lösche nie Datensätze in `mueller_agentdecision` —
  die Tabelle ist append-only."*
- **Instruktion I17**: Die Begründung eines unterdrückten oder umgeleiteten
  Aufrufs gehört in das `mueller_reasoning`-Feld dieses Werkzeugs.

## Stellung im 28-Werkzeuge-Katalog

| Position    | Beschreibung                                                                |
|-------------|------------------------------------------------------------------------------|
| **T17**     | `log_agent_decision` — dieses Werkzeug                                       |
| Kategorie   | Schreib-Werkzeug                                                             |
| Reihenfolge | Erstes Schreib-Werkzeug überhaupt; Voraussetzung für T18 bis T25             |

T18–T25 (record_dunning_event, place_order_on_hold, release_order_hold,
create_payment_plan, update_invoice_extension, create_incident,
route_to_human, create_account_manager_outreach_task) rufen alle nach
Abschluss ihrer eigenen Aktion `log_agent_decision` auf, um den Audit-Eintrag
zu setzen. Dieses Werkzeug ist daher **eine Voraussetzung für 8 weitere**.

## Flow-Diagramm

```
                       ┌────────────────────────────┐
   Agent-Aufruf  ─────► │  Trigger                   │
   (Eingabeparameter)   │  When an agent calls       │
                        │  the flow                  │
                        │  type=Request, kind=Skills │
                        └────────────┬───────────────┘
                                     │
                        ┌────────────▼───────────────┐
                        │  Aktion                    │
                        │  Create_Decision_Record    │
                        │  POST /mueller_agentdecisions │
                        │  Connector: Dataverse      │
                        │  (CommonDataServiceForApps)│
                        └────────────┬───────────────┘
                                     │
                        ┌────────────▼───────────────┐
                        │  Aktion                    │
                        │  Respond_to_the_agent      │
                        │  type=Response, kind=Skills│
                        │  Body: status + decision_id│
                        └────────────┬───────────────┘
                                     │
                                     ▼
                          Antwort an den Agent
```

## Eingabeparameter

Alle Parameter werden vom Agent zur Laufzeit gesetzt. Pflichtfelder erzwingen
einen sinnvollen Mindest-Audit; optionale Felder erweitern den
Aussagegehalt nach Möglichkeit.

| Name              | Typ      | Pflicht | Beschreibung                                                                                  |
|-------------------|----------|:-------:|------------------------------------------------------------------------------------------------|
| `customer_id`     | string   | ✓       | GUID des `account`-Datensatzes, auf den sich die Entscheidung bezieht                         |
| `reasoning`       | string   | ✓       | Klartext-Begründung der Entscheidung (Memo)                                                   |
| `confidence_score`| number   | ✓       | Modell-Konfidenz, Bereich 0,0–1,0; Werte unter 0,6 lösen gemäß Instruktion I10 Routing aus    |
| `model_name`      | string   | ✓       | Identifier des verwendeten Modells, z. B. `gpt-4o`, `custom-classifier-v2`                    |
| `prompt_version`  | string   |         | Versionsbezeichner des Prompt-Templates (Reproduzierbarkeit)                                  |
| `inputs_hash`     | string   |         | SHA-256 oder vergleichbarer Hash der Eingabedaten (für Reproduzierbarkeit)                    |
| `inputs_payload`  | string   |         | JSON-serialisierter Schnappschuss der genutzten Eingaben (Memo)                              |
| `invoice_id`      | string   |         | GUID einer `invoice`, falls die Entscheidung rechnungsspezifisch ist                          |
| `order_id`        | string   |         | GUID eines `salesorder`, falls die Entscheidung auftragsspezifisch ist                        |
| `decided_at`      | string   |         | ISO-8601-Zeitstempel; leer = `utcNow()` zum Aufrufzeitpunkt                                   |
| `override_by_id`  | string   |         | GUID eines `systemuser`, falls ein Mensch die Entscheidung überschrieben hat                  |
| `override_reason` | string   |         | Begründung der menschlichen Überschreibung (Trainingssignal, Memo)                            |

Optionale Lookups (`invoice_id`, `order_id`, `override_by_id`) werden
zur Laufzeit über die Power-Fx-Bedingung
`@if(empty(triggerBody()?['<name>']), null, concat('/<entityset>(', ..., ')'))`
aufgelöst — leere Werte führen zu `null`-Bindings, die Dataverse als „Spalte
nicht setzen" interpretiert.

## Datenmodellbezug

Geschriebene Spalten in `mueller_agentdecision`:

| Spalte                          | Quelle                            | Anmerkung                                        |
|---------------------------------|-----------------------------------|--------------------------------------------------|
| `mueller_customerid`            | `customer_id` (Lookup → account)  | Pflicht                                           |
| `mueller_reasoning`             | `reasoning` (Memo)                | Pflicht                                           |
| `mueller_confidencescore`       | `confidence_score` (Decimal)      | Pflicht; 0,00 – 1,00                             |
| `mueller_modelname`             | `model_name` (String)             | Pflicht                                           |
| `mueller_promptversion`         | `prompt_version` (String)         | Optional                                          |
| `mueller_inputshash`            | `inputs_hash` (String)            | Optional                                          |
| `mueller_inputspayload`         | `inputs_payload` (Memo)           | Optional                                          |
| `mueller_invoiceid`             | `invoice_id` (Lookup → invoice)   | Optional                                          |
| `mueller_orderid`               | `order_id` (Lookup → salesorder)  | Optional                                          |
| `mueller_decidedat`             | `decided_at` (DateTime)           | Optional; leer → `utcNow()`                      |
| `mueller_overrideby`            | `override_by_id` (Lookup → systemuser) | Optional                                     |
| `mueller_overridereason`        | `override_reason` (Memo)          | Optional                                          |

## Ausgabeformat

| Name          | Typ    | Quelle / Wert                                                            |
|---------------|--------|---------------------------------------------------------------------------|
| `status`      | string | Literal `"Logged"` bei Erfolg                                             |
| `decision_id` | string | `@{outputs('Create_Decision_Record')?['body/mueller_agentdecisionid']}` — die GUID des neu erzeugten Audit-Datensatzes |

Der Agent verwendet `decision_id` typischerweise dazu, in einer
nachfolgenden Mahnung oder Protokollantwort den Audit-Bezug mit anzugeben
("Diese Aktion ist unter Audit-ID `<guid>` dokumentiert").

## OptionSet-Limitation in v1

Die Tabelle besitzt drei OptionSet-Spalten, die in dieser Version
**absichtlich nicht beschrieben** werden, weil sie aktuell nur den
Platzhalter `Default` (Wert 727000000) enthalten:

| Spalte                  | Geplante Optionen (gemäß Schema-Shortlist)                                                       |
|-------------------------|---------------------------------------------------------------------------------------------------|
| `mueller_scenario`      | Eines der zehn Geschäftsszenarien                                                                 |
| `mueller_decisiontype`  | `RiskScored` / `EmailSent` / `OrderHeld` / `DisputeRouted` / `PlanProposed` / etc.                |
| `mueller_outcome`       | `Executed` / `Suppressed` / `RoutedToHuman` / `Overridden`                                        |

**Folgeaufgabe**: ein separates Skript `provision_optionsets.py`, das die
OptionSet-Metadaten via `Microsoft.Dynamics.CRM.UpdateOptionValue` mit den
realen Optionen erweitert. Sobald das geschehen ist, wird `flow_definition.py`
um drei zusätzliche Trigger-Eingabeparameter erweitert (`scenario_value`,
`decision_type_value`, `outcome_value`) und das CreateRecord um die
zugehörigen `item/<spalte>` Zuweisungen ergänzt.

In v1 wird der Entscheidungstyp informell als Präfix in `mueller_reasoning`
übergeben (z. B. *„decision_type=send_dunning. Customer xyz is 30 days late
…"*). Das ist eine bewusste Übergangslösung.

## Trigger-Beschreibung für die generative Orchestrierung

Microsoft empfiehlt, in der Werkzeugbeschreibung präzise anzugeben, **wofür**
das Werkzeug zu verwenden ist und **wofür nicht**. Empfohlener Text für die
*Description* im Maker (oder im `description`-Feld des Workflow-Eintrags):

> *Schreibt einen unveränderlichen Audit-Trail-Eintrag (`mueller_agentdecision`)
> für eine Agent-Entscheidung. Verwende dieses Werkzeug nach jeder
> Aktion, die einen Kundendatensatz beeinflusst — Mahnung, Auftragssperre,
> Ratenplanvorschlag, Eskalation an Mensch. Nicht für Lese-Aktionen oder
> für rein interne Modell-Schritte verwenden. Pflichtparameter:
> `customer_id`, `reasoning`, `confidence_score`, `model_name`. Gibt die
> GUID des erzeugten Audit-Datensatzes zurück.*

## Deployment

Voraussetzungen:

- `.env` in der Repository-Wurzel mit `DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`,
  `DATAVERSE_CLIENT_SECRET`, `DATAVERSE_ENVIRONMENT_URL` (siehe `.env.example`).
- Lokale Python-Umgebung mit `msal` und `requests` (`pip install -e .` aus
  der Repository-Wurzel installiert die Abhängigkeiten).
- Die Lösung `mueller_demo_solution` muss im Ziel-Dataverse existieren.
- Eine Connection-Reference mit logischem Namen
  `new_sharedcommondataserviceforapps_14a83` existiert in der Umgebung
  (wird vom bestehenden Werkzeug 1 wiederverwendet).

Befehle:

```bash
# Trockenlauf — JSON ausgeben, kein POST
python copilot_studio/tools/log_agent_decision/deploy.py --dry-run

# Erstellen (statecode=0, also Entwurf — keine Aktivierung)
python copilot_studio/tools/log_agent_decision/deploy.py

# Erstellen und sofort aktivieren (statecode=1)
python copilot_studio/tools/log_agent_decision/deploy.py --activate

# Anderen Flow-Namen verwenden
python copilot_studio/tools/log_agent_decision/deploy.py --name LogAgentDecisionV2
```

`deploy.py` ist **idempotent**: ein erneuter Aufruf mit demselben
`--name` erkennt den bestehenden Datensatz und endet ohne `POST`.

## Registrierung als Werkzeug am Agent

Das Anlegen des Workflow-Datensatzes ist **getrennt** von der Registrierung
als Werkzeug am Agent. Microsoft Learn dokumentiert für die Werkzeug-
Registrierung ausschließlich den UI-Pfad. Nach erfolgreichem Deployment:

1. Copilot Studio Maker öffnen → AR-Agent auswählen.
2. *Tools* → *Add a tool* → *Flow*.
3. `LogAgentDecision` aus der Liste wählen → *Add and configure*.
4. *Description* überschreiben mit dem unter „Trigger-Beschreibung" oben
   formulierten Text. Microsoft warnt explizit: die voreingestellte
   Beschreibung wiederholt nur den Namen — die generative Orchestrierung kann
   darauf nicht routen.

## Verifikation

| Schritt | Erwartung                                                                                                         |
|---------|-------------------------------------------------------------------------------------------------------------------|
| 1. Dry-run | `python deploy.py --dry-run` druckt das vollständige `clientdata`-JSON, Exit-Code 0                            |
| 2. Erstellung | `python deploy.py` antwortet mit `Created workflow <guid>`, Exit-Code 0                                     |
| 3. Idempotenz | Zweiter Aufruf druckt `Workflow LogAgentDecision already exists at <guid>`, kein erneutes Anlegen           |
| 4. Solution | `GET /solutioncomponents?$filter=_solutionid_value eq <sol> and componenttype eq 29` enthält den neuen GUID    |
| 5. UI-Sichtbarkeit | Im Copilot-Studio-Maker erscheint `LogAgentDecision` unter *Add a tool* → *Flow*                       |
| 6. Smoke-Test | Test-Pane: *„Protokolliere die Entscheidung X für Kunde Y mit Konfidenz 0,85 vom Modell gpt-4o"*; Antwort enthält `decision_id` |
| 7. DB-Spur | `GET /mueller_agentdecisions?$filter=mueller_modelname eq 'gpt-4o'&$orderby=createdon desc&$top=1` liefert den neuen Datensatz |
| 8. Code-Stil | `grep -E '^\s*#\|"""\|'''' deploy.py flow_definition.py` liefert null Treffer (keine Kommentare, keine Docstrings) |

## Bekannte Einschränkungen

- **OptionSet-Spalten werden nicht geschrieben** — siehe oben.
- **Keine programmatische Werkzeug-Registrierung am Agent** — Microsoft bietet
  zum aktuellen Zeitpunkt keine dokumentierte API dafür; UI-Schritt bleibt
  manuell.
- **Connection-Reference wird wiederverwendet, nicht erzeugt** — die in
  Werkzeug 1 (`Get Customer Open Invoices`) bereits angelegte Verbindung wird
  geteilt. Beim Anlegen einer neuen Umgebung muss diese einmalig im
  Maker erstellt werden.
- **Authentifizierung zur Laufzeit**: Der Flow läuft unter der Identität, die
  Copilot Studio dem Agent zur Laufzeit bereitstellt — nicht unter der
  Identität der Deploy-Skript-App. Die Deploy-App benötigt nur Schreibrechte
  auf das `workflow`-Tabelle, nicht auf `mueller_agentdecision`.

## Hintergrund: warum die Trigger-/Antwort-Struktur kein Geheimnis ist

Microsoft Learn beschreibt die Anforderungen an einen Agent-Flow
(„*When an agent calls the flow*"-Trigger plus „*Respond to the agent*"-Aktion),
veröffentlicht aber **nicht** das exakte JSON der Logic-Apps-
Workflow-Definition-Language. Diese Implementierung extrahiert die
JSON-Struktur einmalig aus dem bereits laufenden Werkzeug 1 (`Get Customer
Open Invoices`) der Quell-Umgebung — der Trigger-Schlüssel `manual` mit
`type=Request`, `kind=Skills`, sowie die Antwort-Aktion `Respond_to_the_agent`
mit `type=Response`, `kind=Skills`. Damit ist v1 datengetrieben, nicht
spekulativ.
