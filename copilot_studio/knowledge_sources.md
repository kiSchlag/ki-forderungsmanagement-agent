# Wissensquellen

Microsoft Copilot Studio erlaubt **maximal 15 Dataverse-Tabellen pro
Wissensquelle** (siehe
[Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-dataverse)).
Genau diese 15 Tabellen werden als Wissensquelle des Müller-AR-Agents
konfiguriert.

## Voraussetzung

Bevor Dataverse-Tabellen als Wissensquelle eingebunden werden können, muss
in der Power-Platform-Admin-Konsole **Dataverse Search** für die Umgebung
aktiviert sein. Microsoft formuliert dies als zwingende Voraussetzung.

## Konfiguration

Für jede Tabelle wird im Maker eine **kurze, präzise Beschreibung**
hinterlegt — sie ist der primäre Hinweis für die generative Orchestrierung,
welche Tabelle für eine konkrete Anfrage relevant ist.

| #   | Tabelle (logical name)                | Beschreibung für den Maker                                                       |
|----:|---------------------------------------|----------------------------------------------------------------------------------|
|  1  | `account`                             | Geschäftskunden mit Stammdaten, Limits, Segment und Loyalitätsmerkmalen          |
|  2  | `contact`                             | Ansprechpartner*innen pro Geschäftskunde mit Anrede und Kommunikationsdaten      |
|  3  | `customeraddress`                     | Liefer- und Rechnungsadressen pro Kunde                                          |
|  4  | `invoice`                             | Ausgangsrechnungen mit Beträgen, Fälligkeit und Müller-spezifischen AR-Feldern   |
|  5  | `invoicedetail`                       | Einzelne Positionen einer Rechnung                                               |
|  6  | `salesorder`                          | Kundenaufträge mit Sperrstatus                                                   |
|  7  | `mueller_paymentevent`                | Verbuchte Zahlungseingänge mit Zahlungsmittel und Datum                          |
|  8  | `mueller_dunningevent`                | Mahnungen pro Kunde mit Stufe und Versandkanal                                   |
|  9  | `mueller_riskscoresnapshot`           | Tägliche Snapshots der Risikobewertung pro offener Rechnung                      |
| 10  | `mueller_creditsignal`                | Externe Bonitätssignale (Creditreform-Hinweise, Insolvenzdatenbank)              |
| 11  | `mueller_paymentplan`                 | Vereinbarte Ratenpläne mit Anzahl Raten und Status                               |
| 12  | `mueller_communicationpreference`     | Tonart, Anrede, Sprache, Ruhezeiten pro Kunde                                    |
| 13  | `mueller_lifetimevaluesnapshot`       | Berechneter Customer-Lifetime-Value pro Kunde (Snapshot)                         |
| 14  | `mueller_agentdecision`               | Audit-Trail aller Agent-Entscheidungen mit Begründung und Konfidenz              |
| 15  | `incident`                            | Reklamationen und Streitfälle mit Bearbeitungsstatus                             |

## Über Werkzeuge erreichbar (kein Wissensquellen-Slot)

Die übrigen kundenspezifischen Müller-Tabellen werden **nicht** als
Wissensquelle eingebunden — der Agent ruft sie über dedizierte Werkzeuge
ab. Das schützt das 15er-Kontingent für die häufigsten Such-Treffer:

- `mueller_sepamandate` (über T9 `get_active_sepa_mandate`)
- `mueller_churnsignal` (über T8 `get_active_churn_signals`)
- `mueller_deliverynote` (über T16 `find_delivery_note_for_invoice`)
- `mueller_paymentplaninstallment` (Detailtabelle zu T21)
- `contract`, `contractdetail` (über T12 `get_contracts_due_for_renewal`)
- `product`, `pricelevel` (Stammdaten ohne KI-Suchwert)
