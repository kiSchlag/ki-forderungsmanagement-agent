# Werkzeug: `Get Customer Open Invoices`

> **Status**: produktiv. Erstes implementiertes Werkzeug des Müller-AR-Agents.
> Gebaut über die Copilot-Studio-Maker-Oberfläche als **Agent-Flow**
> (Power-Automate-Cloud-Flow).

## Zweck

Liefert für einen genannten Kunden alle offenen Rechnungen, sortiert nach
Außenstand absteigend. Beantwortet typische Anfragen wie:

- *„Was schuldet mir Schmidt Maschinenbau aktuell?"*
- *„Welche Rechnungen sind bei der Weber Logistik AG noch offen?"*
- *„Zeig mir die fünf höchsten offenen Posten von ABC Industrie GmbH."*

## Verhältnis zum geplanten 28-Werkzeuge-Katalog

Das Werkzeug ist eine **bewusste Hybrid-Form** zwischen zwei Werkzeugen aus
der ursprünglichen Katalog-Planung:

- **T1 `get_high_risk_invoices`**: liefert offene Rechnungen, aber gefiltert
  auf einen Risikoschwellwert.
- **T6 `get_customer_open_orders`**: liefert offene **Aufträge** (nicht
  Rechnungen) eines bestimmten Kunden.

In der Praxis hat sich gezeigt, dass „offene Rechnungen je Kunde" die
häufigste Anfrage ist — sie erscheint direkt oder indirekt in den
Szenarien 1 (Vorausschau), 4 (Stundung), 5 (Auftragssperre), 6
(Reklamation) und 7 (Reporting). Statt diese Anfrage über zwei verkettete
Werkzeuge zu lösen, ist sie als ein eigenständiger Agent-Flow modelliert.

## Eingabeparameter

| Parameter        | Typ        | Erforderlich | Beschreibung                                                              |
|------------------|------------|:------------:|---------------------------------------------------------------------------|
| `customer_name`  | String     |      ✓       | Kundenname oder Teilstring (Substring-Suche, Groß-/Kleinschreibung egal) |
| `top`            | Integer    |              | Optional: maximale Trefferzahl (Standard: alle)                           |
| `min_amount`     | Decimal    |              | Optional: nur Rechnungen mit Außenstand ≥ diesem Betrag in EUR           |

## Datenmodellbezug

| Tabelle                     | Verwendete Spalten (Auszug)                                                                  |
|-----------------------------|----------------------------------------------------------------------------------------------|
| `account`                   | `accountid`, `name`, `accountnumber`                                                         |
| `invoice`                   | `invoiceid`, `invoicenumber`, `name`, `duedate`, `totalamount`, `statecode`                  |
| `invoice` (Müller-Felder)   | `mueller_outstandingamount`, `mueller_paidamount`, `mueller_riskscore`, `mueller_partiallypaid`, `mueller_isdisputed` |

## Filterlogik (OData)

```
$filter = (statecode eq 0 or mueller_outstandingamount gt 0)
          and contains(customerid_account/name, '<customer_name>')
$orderby = mueller_outstandingamount desc
$expand  = customerid_account($select=name,accountnumber)
```

Eine Rechnung gilt als „offen", wenn sie aktiv ist (statecode = 0)
**oder** noch einen positiven Außenstand trägt — Letzteres deckt teilweise
bezahlte Rechnungen ab, deren statecode bereits geschlossen ist.

## Ausgabeformat

Eine sortierte JSON-Liste pro offener Rechnung mit den folgenden Feldern:

| Feld              | Typ      | Beschreibung                                                  |
|-------------------|----------|---------------------------------------------------------------|
| `invoicenumber`   | String   | Müller-Rechnungsnummer (z. B. `RE-2026-01287`)               |
| `customer`        | String   | Kundenname                                                    |
| `accountnumber`   | String   | Kundennummer                                                  |
| `duedate`         | Date     | Fälligkeitsdatum                                              |
| `days_overdue`    | Integer  | Tage Überschreitung (negativ wenn Fälligkeit in Zukunft)      |
| `totalamount`     | Decimal  | Brutto in EUR                                                 |
| `outstanding`     | Decimal  | Offener Betrag in EUR                                         |
| `risk`            | Decimal  | Risikobewertung (0,00–1,00)                                   |
| `partially_paid`  | Boolean  | Ist die Rechnung teilweise bezahlt?                           |
| `disputed`        | Boolean  | Liegt eine Reklamation vor?                                   |

## Trigger und Aufruf durch den Agent

Das Werkzeug wird durch die generative Orchestrierung ausgewählt, sobald
eine Benutzeranfrage einen **Kundennamen** enthält und sinngemäß nach
**offenen Rechnungen, Forderungen, Außenständen** oder **unbezahlten
Rechnungen** fragt. Eine Beschreibung dieser Auslöseworte ist im Maker
unter *Beschreibung* des Agent-Flows hinterlegt.

## Audit-Verhalten

Jeder Aufruf wird im Anschluss durch das Werkzeug **T17 `log_agent_decision`**
in `mueller_agentdecision` protokolliert — mit dem Suchparameter, der
Trefferzahl und der Antwortlatenz.

## Lokales Pendant zur Verifikation

Während der Entwicklungsphase kann dieselbe Abfrage lokal via Skript
ausgeführt werden:

```bash
python -m ki_forderungsmanagement.list_open_invoices --customer "Schmidt Maschinenbau"
```

Damit lassen sich Ergebnisse zwischen lokaler Implementierung und
produktivem Agent-Flow gegenprüfen.
