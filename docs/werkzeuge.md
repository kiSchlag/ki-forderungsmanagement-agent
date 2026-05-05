# Werkzeugkatalog

Dieser Katalog listet die **28 Werkzeuge** auf, die der KI-Agent zur
Umsetzung der zehn [Geschäftsszenarien](szenarien.md) benötigt. Die Anzahl
liegt bewusst am oberen Ende der von Microsoft empfohlenen Spanne von
**25 bis 30 Werkzeugen** pro Agent (Maximum 128 — siehe
[Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-tools-custom-agent)),
denn jedes weitere Werkzeug erhöht die Komplexität der generativen
Orchestrierung.

## Begriffsklärung

Microsoft Copilot Studio kennt sechs Werkzeugtypen: **Prompt**,
**Agent-Flow** (ein Power-Automate-Cloud-Flow, der als Werkzeug exponiert
wird), **Computer Use**, **Custom Connector**, **Model Context Protocol**
und **REST API**. Für die Müller-AR-Implementierung sind ausschließlich
**Agent-Flows** und **Prompts** vorgesehen.

## Übersicht

| Kategorie               | Anzahl | Wirkrichtung                                                   |
|-------------------------|-------:|----------------------------------------------------------------|
| **Lese-Werkzeuge**      |    16  | Daten aus Dataverse abrufen, ohne sie zu verändern             |
| **Schreib-Werkzeuge**   |     9  | Records in Dataverse anlegen oder aktualisieren                |
| **Kompositions-Werkzeuge** |  3  | Inhalte erzeugen — E-Mails verfassen, klassifizieren, bündeln  |

---

## Lese-Werkzeuge (T1–T16)

| #   | Werkzeug                              | Rückgabewert                                                                |
|----:|---------------------------------------|------------------------------------------------------------------------------|
| T1  | `get_high_risk_invoices`              | Rechnungen mit Risikobewertung über Schwellwert                              |
| T2  | `get_invoice_risk_explanation`        | Top-Faktoren, die den Risikowert einer Rechnung treiben                      |
| T3  | `get_customer_payment_baseline`       | Durchschnittliche Tage-bis-Zahlung + Standardabweichung pro Kunde            |
| T4  | `get_recent_credit_signals`           | Creditreform- und Insolvenzhinweise der letzten 60 Tage                      |
| T5  | `get_customer_communication_profile`  | Segment, Tonart, Formalität, Anrede, Sprache                                 |
| T6  | `get_customer_open_orders`            | Aktive Aufträge mit Beträgen für einen Kunden                                |
| T7  | `get_customer_ltv`                    | Aktuellster Lifetime-Value-Snapshot                                          |
| T8  | `get_active_churn_signals`            | Abwanderungssignale für einen Kunden                                         |
| T9  | `get_active_sepa_mandate`             | Aktives SEPA-Lastschriftmandat eines Kunden                                  |
| T10 | `get_orders_to_hold`                  | Aufträge von Kunden, die ihre Limits überschreiten                           |
| T11 | `get_disputes_in_progress`            | Aktive Reklamationen nach Bearbeitungsdauer                                  |
| T12 | `get_contracts_due_for_renewal`       | Verträge, deren Renewal-Auslöser überschritten ist                           |
| T13 | `get_decision_history_for_customer`   | Alle Agent-Entscheidungen zu einem Kunden                                    |
| T14 | `get_daily_dso_briefing`              | Heutiger DSO + offener Saldo + Top-Risiken                                   |
| T15 | `get_weekly_aging_report`             | Aging-Buckets aufgeschlüsselt nach Kundensegment                             |
| T16 | `find_delivery_note_for_invoice`      | URL des Lieferschein-PDF in SharePoint                                       |

## Schreib-Werkzeuge (T17–T25)

| #   | Werkzeug                                  | Aktion                                                                  |
|----:|-------------------------------------------|--------------------------------------------------------------------------|
| T17 | `log_agent_decision`                      | Schreibt einen Append-only-Eintrag in `mueller_agentdecision`            |
| T18 | `record_dunning_event`                    | Schreibt einen Mahnungs-Vorgang in `mueller_dunningevent`                |
| T19 | `place_order_on_hold`                     | Setzt die Sperrfelder am `salesorder`                                    |
| T20 | `release_order_hold`                      | Entfernt die Sperrfelder am `salesorder`                                 |
| T21 | `create_payment_plan`                     | Legt Plan + Raten an (`mueller_paymentplan` + Installments)              |
| T22 | `update_invoice_extension`                | Setzt `mueller_extensiongranted` auf der Rechnung                        |
| T23 | `create_incident`                         | Legt einen Reklamationsfall (`incident`) an                              |
| T24 | `route_to_human`                          | Setzt `routedtouserid` und unterdrückt Auto-Aktionen                     |
| T25 | `create_account_manager_outreach_task`    | Erzeugt eine Aufgabe für die Kundenbetreuung                             |

## Kompositions-Werkzeuge (T26–T28)

| #   | Werkzeug                | Aktion                                                                        |
|----:|-------------------------|-------------------------------------------------------------------------------|
| T26 | `draft_dunning_email`   | Verfasst Betreff + Text einer deutschsprachigen Mahnung; tonangepasst         |
| T27 | `classify_inbound_email`| Liefert Klassifikation, Sentiment und Konfidenz für eine eingehende E-Mail    |
| T28 | `build_dispute_dossier` | Bündelt alle reklamationsrelevanten Belege in ein Dossier                     |

---

## Aktueller Implementierungsstand

**Produktiv im Einsatz** (über die Copilot-Studio-Maker-Oberfläche gebaut):

- **`Get Customer Open Invoices`** — eine Agent-Flow-Realisierung, die für
  einen Kunden alle offenen Rechnungen sortiert nach Außenstand liefert.
  Das Werkzeug ist eine **Hybrid-Form** zwischen T1 (`get_high_risk_invoices`)
  und einem kundengefilterten Lese-Werkzeug; es wird vom Agent
  als erstes Werkzeug zur Beantwortung von „Was schuldet mir Kunde X?"-Fragen
  verwendet. Detailspezifikation:
  [`copilot_studio/tools/get_customer_open_invoices.md`](../copilot_studio/tools/get_customer_open_invoices.md).

**In Planung**: die übrigen 27 Werkzeuge der oben aufgeführten Kataloge.

## Reihenfolge der Implementierung

Die Reihenfolge folgt zwei Prinzipien: (1) Audit-Trail-Werkzeuge zuerst,
weil jedes andere Schreib-Werkzeug von ihnen abhängt; (2) je früher ein
Werkzeug einen Demo-Effekt hat, desto höher seine Priorität.

1. **Audit-Fundament**: T17 (`log_agent_decision`)
2. **Einfachstes Lese-Werkzeug für die Agentenintegration**: T3 (`get_customer_payment_baseline`)
3. **Demo-Wirkung**: T14 (`get_daily_dso_briefing`) — bündelt vier Tabellen zu einem aussagekräftigen CFO-Bericht
4. **Komposition**: T26 (`draft_dunning_email`) — sichtbarste Agent-Wirkung im Posteingang
5. **Restliche Werkzeuge** in beliebiger Reihenfolge mit Tests pro Werkzeug
6. **Themen** (5 Stück) erst, wenn alle benötigten Werkzeuge stabil laufen
