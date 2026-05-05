# KI-Forderungsmanagement-Agent

> Microsoft 365 · Copilot Studio · Dataverse · Power Platform · DACH-Mittelstand

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)]()
[![Lizenz](https://img.shields.io/badge/Lizenz-MIT-green)]()
[![Status](https://img.shields.io/badge/Status-In%20Entwicklung-orange)]()
[![Sprache](https://img.shields.io/badge/Dokumentation-Deutsch-yellow)]()

## Überblick

Dieses Repository zeigt, wie ein autonomer KI-Agent für das **Forderungsmanagement
(Accounts Receivable, AR)** eines deutschen B2B-Mittelstandsunternehmens auf
Basis der Microsoft-Plattform aufgebaut wird. Der Agent läuft in **Microsoft
Copilot Studio**, greift über **Dataverse** auf alle Kunden-, Rechnungs- und
Aktivitätsdaten zu und führt Aktionen über **Power-Automate-Agent-Flows** aus.

Die Demo-Umgebung ist die fiktive **Müller Industriebedarf GmbH**, ein
B2B-Distributor für industrielle Software- und Hardwareprodukte mit
2.400 aktiven Geschäftskunden im DACH-Raum.

## Geschäftsproblem

Der Müller-Vertrieb hat eine **Days Sales Outstanding (DSO) von 47 Tagen** —
die Branchenkennzahl liegt bei **32 Tagen**. Das ist ein finanzieller Hebel
in zweistelliger Millionenhöhe pro Jahr, ausgelöst durch:

- **Reaktive statt vorausschauende Mahnabläufe**: Mahnungen gehen erst
  raus, wenn die Frist bereits überschritten ist.
- **Einheitliche Kommunikation für alle Kunden**: derselbe Mahnton an
  zehnjährige Stammkunden wie an Erstbesteller.
- **Manuelle Bearbeitung eingehender Kunden-E-Mails**: jede
  Anfrage wird zuerst gelesen, dann zugeordnet, dann beantwortet — ohne
  Vorverdichtung.
- **Fehlende Eskalationslogik**: weder Bonitätssignale noch
  Lifetime-Value-Daten fließen in die Entscheidung ein.

Der Agent adressiert genau diese Lücken — vorausschauend, segmentiert,
nachvollziehbar.

## Architektur auf einen Blick

```
┌────────────────────┐    ┌────────────────────┐    ┌─────────────────────┐
│  Microsoft 365     │    │  Copilot Studio    │    │  Dataverse          │
│  Outlook / Teams   │◄──►│  Generative        │◄──►│  Standard- und      │
│  SharePoint        │    │  Orchestrierung    │    │  Müller-Tabellen    │
└────────────────────┘    └─────────┬──────────┘    └─────────────────────┘
                                    │
                          ┌─────────▼──────────┐    ┌─────────────────────┐
                          │  Power Automate    │◄──►│  Externe Signale    │
                          │  Agent-Flows       │    │  Creditreform, SEPA │
                          └────────────────────┘    └─────────────────────┘
```

- **Wissensquellen**: bis zu 15 Dataverse-Tabellen pro Wissensquelle
  ([Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-add-dataverse))
- **Werkzeuge**: 28 Werkzeuge in den Kategorien *Lesen / Schreiben /
  Komposition* — bewusst am oberen Ende der von Microsoft empfohlenen
  Spanne von 25–30 ([Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-tools-custom-agent))
- **Orchestrierung**: generativ — der Agent wählt Werkzeuge, Themen und
  Wissensquellen je nach Anliegen selbst aus
  ([Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions))

## Die zehn Geschäftsszenarien

Eine ausführliche Beschreibung jedes Szenarios mit allen Unterpunkten und
Datenmodellbezug finden Sie in [`docs/szenarien.md`](docs/szenarien.md).

| Nr. | Szenario                                | Kernidee                                                              |
|----:|-----------------------------------------|-----------------------------------------------------------------------|
|  1  | Vorausschauende Zahlungsausfallerkennung | Risiko bewerten, **bevor** die Rechnung überfällig wird               |
|  2  | Kundengruppen-spezifische Kommunikation | Loyale Stammkunden anders ansprechen als Erstbesteller                |
|  3  | Eingehende E-Mails verstehen + beantworten | Klassifizieren, beantworten oder mit Kontext an Mensch übergeben    |
|  4  | Stundungen und Ratenpläne automatisieren | Innerhalb klar definierter Regeln eigenständig anbieten               |
|  5  | Auftragssperre und -freigabe            | Bestellung neuer Ware blockieren, sobald Saldo Limit übersteigt       |
|  6  | Reklamationen und Streitfälle bearbeiten | Belege bündeln, Eigentümer zuweisen, Bearbeitungszeit messen          |
|  7  | CFO- und Management-Reporting           | Tägliche, wöchentliche und Live-Berichte automatisiert erzeugen       |
|  8  | Kundenwert und Abwanderungsschutz       | Aggressive Mahnung gegen Beziehungswert abwägen                       |
|  9  | Produkt- und vertragsspezifische Logik  | Software-Renewal vs. Hardware-Meilenstein vs. Wartungsvertrag         |
| 10  | Compliance und Audit-Trail              | Jede Entscheidung protokollieren, GDPR-konform                        |

## Der Agent im Detail

### Komponenten des Agents

Ein Copilot-Studio-Agent setzt sich gemäß der offiziellen Microsoft-Dokumentation
aus **sechs Bausteinen** zusammen:

| # | Komponente            | Bedeutung                                                                         |
|--:|-----------------------|------------------------------------------------------------------------------------|
| 1 | **Anweisungen**       | Zentrales Regelwerk, das das Verhalten des Agents lenkt (siehe `docs/instruktionen.md`) |
| 2 | **Werkzeuge**         | Konkrete Aktionen: Agent-Flows, REST-APIs, MCP-Server, benutzerdefinierte Konnektoren, *Computer Use*, Prompts |
| 3 | **Wissensquellen**    | Dataverse-Tabellen, mit denen sich der Agent eine Wissensbasis aufbaut             |
| 4 | **Themen**            | Deterministische Konversationsabläufe für besonders sensible oder verbindliche Vorgänge |
| 5 | **Orchestrierung**    | Generativ (Standard) oder klassisch — bestimmt, wie der Agent Antworten plant      |
| 6 | **Auslöser**          | Was eine Konversation startet (Benutzernachricht, Ereignis, Zeitplan)              |

### Werkzeugkatalog

28 Werkzeuge, gruppiert nach Wirkrichtung. Die vollständige Liste mit Beschreibung,
Eingabe- und Ausgabeparametern finden Sie in [`docs/werkzeuge.md`](docs/werkzeuge.md).

| Typ           | Anzahl | Zweck                                                                       |
|---------------|-------:|-----------------------------------------------------------------------------|
| **Lese-Werkzeuge**       |    16  | Rechnungen, Zahlungen, Risiko, Kommunikationsprofile, Verträge abrufen     |
| **Schreib-Werkzeuge**    |     9  | Entscheidungen protokollieren, Sperren setzen, Ratenpläne anlegen, Eskalieren |
| **Kompositions-Werkzeuge** |   3  | Mahnschreiben verfassen, eingehende E-Mails klassifizieren, Streitfall-Dossiers bauen |

### Wissensquellen

15 Dataverse-Tabellen werden als Wissensquellen eingebunden — das von Microsoft
festgelegte Maximum pro Wissensquelle:

`account` · `contact` · `customeraddress` · `invoice` · `invoicedetail` ·
`salesorder` · `mueller_paymentevent` · `mueller_dunningevent` ·
`mueller_riskscoresnapshot` · `mueller_creditsignal` · `mueller_paymentplan` ·
`mueller_communicationpreference` · `mueller_lifetimevaluesnapshot` ·
`mueller_agentdecision` · `incident`

Die übrigen Müller-Tabellen (SEPA-Mandate, Lieferscheine, Vertragspositionen
usw.) sind nicht als Wissensquelle, sondern über Werkzeuge erreichbar.

## Microsoft 365 Integration

| Microsoft-365-Dienst | Berührungspunkte mit dem Agent                                                |
|----------------------|-------------------------------------------------------------------------------|
| **Outlook**          | Kundenkommunikation per E-Mail, Übergabe an Sachbearbeiter*in                |
| **Teams**            | Eskalation an Vertrieb, einklick-Override für Auftragssperren                |
| **SharePoint**       | Lieferscheine, signierte Lieferbestätigungen für Beweisführung               |
| **Power BI**         | Live-DSO-Dashboard, Aging-Buckets, Cash-Forecast                             |
| **Entra ID**         | Authentifizierung über vertrauliche Client-App                              |

## Implementierungsstand

**Produktiv im Einsatz auf dem Agent `Agent Müller "Zahlungsassistent"` (Umgebung `dso-demo`):**

1. **`Get Customer Open Invoices`** — Agent-Flow, der für einen Kunden alle
   offenen Rechnungen sortiert nach Außenstand liefert. Bereitstellung über
   die Copilot-Studio-Maker-Oberfläche. Detailspezifikation in
   [`copilot_studio/tools/get_customer_open_invoices/README.md`](copilot_studio/tools/get_customer_open_invoices/README.md).
2. **`Log Agent Decision`** (zugrunde liegender Workflow `LogAgentDecisionV2`) —
   Audit-Trail-Schreibwerkzeug; protokolliert jede sinnvolle Agent-Entscheidung
   in `mueller_agentdecision`. Voraussetzung für **alle** weiteren acht
   Schreib-Werkzeuge (T18–T25). Bereitstellung kombinierte Python-SDK-
   Erzeugung der Workflow-Definition mit Maker-Aktivierung — siehe
   [`copilot_studio/tools/log_agent_decision/README.md`](copilot_studio/tools/log_agent_decision/README.md)
   für die Erkenntnisse zur Connection-Authorization-Hürde und zur
   Skills-Trigger-Schlüssel-Umbenennung.

**Vorbereitet** (Schema, Daten, Authentifizierung steht):

- 15 Müller-Tabellen über `provision_schema.py` automatisiert provisioniert
- 3.000 deterministisch erzeugte Demo-Kunden mit 31.309 Rechnungen über
  `generate_synthetic_data.py` aufsetzbar
- Vereinheitlichtes Konfigurationsmodell, Retry-Logik und Pagination im
  geteilten `http_client.py`

**Geplant**:

- 26 weitere Werkzeuge (siehe Werkzeugkatalog) als Agent-Flows oder
  Prompt-Werkzeuge
- 5 deterministische Themen für sensible Abläufe (Genehmigung,
  menschliche Übergabe, Ratenplanvereinbarung)
- Anbindung externer Bonitätssignale (Creditreform-Web-Service)

## Engineering-Praktiken

### Schemaprovisierung als Code

Die elf kundenspezifischen Tabellen und über 100 zusätzlichen Spalten werden
nicht händisch im Maker erstellt, sondern über die Web-API
([`provision_schema.py`](src/ki_forderungsmanagement/provision_schema.py))
aus der versionierten
[`data/scenario_table_shortlist.json`](data/scenario_table_shortlist.json)
provisioniert. Ein Standard-`--dry-run` listet alle bevorstehenden Aktionen
auf, ein `--apply` führt sie idempotent aus (Existenz-Probes vor jedem
`POST`).

### Deterministische synthetische Daten

`generate_synthetic_data.py --seed 42` erzeugt **byte-identische**
Demo-Daten — über NumPy-RNG, deterministisches Faker-Locale `de_DE`,
echte deutsche PLZ/BLZ/USt-IdNr-Tabellen und mod-97-validierte IBANs.
Reproduzierbarkeit ist hier kein Nebeneffekt, sondern Voraussetzung
dafür, dass Tests und Vorführungen vergleichbar bleiben.

15 fest verdrahtete *Story-Patterns* (z. B. *About-To-Insolve*,
*Repeat-Late-Payer*, *Loyal-VIP*) werden über eine Verifikationsroutine
geprüft, bevor `--apply` Daten in Dataverse einfügt — schlägt eine
Story fehl, bricht der Lauf ab.

### Sicherheitsmodell

- Authentifizierung über **Microsoft Entra ID** (vertraulicher Client),
  niemals über Benutzerkonten
- Geheimnisse ausschließlich in `.env` (per `.gitignore` ausgeschlossen) —
  keine hartcodierten Credentials im Quelltext
- **`Retry-After`-konformes Drosselungsverhalten** gemäß
  [Microsoft API-Limits](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits)
- Audit-Tabelle `mueller_agentdecision`: jede Agentenentscheidung wird mit
  Begründung, Konfidenzwert und Modellname **append-only** dokumentiert

## Schnellstart

```bash
# 1. Klonen
git clone https://github.com/kiSchlag/ki-forderungsmanagement-agent.git
cd ki-forderungsmanagement-agent

# 2. Python-Umgebung
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Konfiguration: .env anlegen (siehe .env.example)
cp .env.example .env
# Tragen Sie Ihre Entra-ID- und Dataverse-Werte in .env ein

# 4. Schema in Dataverse provisionieren (Probe-Lauf)
python -m ki_forderungsmanagement.provision_schema

# 5. Synthetische Daten erzeugen (Probe-Lauf)
python -m ki_forderungsmanagement.generate_synthetic_data --customers 100

# 6. Offene Rechnungen abfragen (live aus Dataverse)
python -m ki_forderungsmanagement.list_open_invoices --top 20
```

Detaillierte Aufrufbeispiele und Optionen zu jedem Skript stehen in
den jeweiligen Modul-Docstrings.

## Repository-Aufbau

```
ki-forderungsmanagement-agent/
├── README.md                              ← Sie lesen dieses Dokument
├── LICENSE                                ← MIT
├── .env.example                           ← Vorlage; echte Werte in lokaler .env
├── pyproject.toml + requirements.txt      ← Abhängigkeiten + Konsolen-Skripte
├── docs/                                  ← Vertiefende Fachdokumentation (DE)
│   ├── szenarien.md                       ← 10 Szenarien mit Datenmodellbezug
│   ├── werkzeuge.md                       ← 28-Werkzeuge-Katalog im Detail
│   ├── instruktionen.md                   ← 17 Anweisungsregeln
│   └── architektur.md                     ← Datenflüsse + M365-Integration
├── src/ki_forderungsmanagement/           ← Python-Paket (Code in Englisch)
│   ├── config.py                          ← Einheitliche Konfiguration
│   ├── http_client.py                     ← MSAL-Auth, Retry, Pagination, $batch
│   ├── export_metadata.py                 ← Dataverse-Schema vollständig exportieren
│   ├── build_shortlist.py                 ← Schema-Shortlist aus Dump bauen
│   ├── provision_schema.py                ← Tabellen + Spalten provisionieren
│   ├── generate_synthetic_data.py         ← 3000 deterministische Kunden
│   ├── seed_invoices.py                   ← Realistische offene Rechnungen
│   └── list_open_invoices.py              ← AR-Live-Abfrage
├── data/                                  ← Versionierte Schemata
│   └── scenario_table_shortlist.json      ← Schema-Vertrag (188 KB)
└── copilot_studio/                        ← Agent-Artefakte
    ├── instructions.md                    ← Master-Prompt
    ├── knowledge_sources.md               ← 15 Wissensquellen mit Beschreibung
    └── tools/
        ├── get_customer_open_invoices/README.md ← Werkzeug 1 (produktiv, via Maker-UI)
        └── log_agent_decision/                  ← Werkzeug 2 (produktiv als LogAgentDecisionV2)
            ├── README.md                        ← Spezifikation, Erkenntnisse, Audit-Datensätze
            ├── flow_definition.py               ← Referenz-JSON-Erzeugung
            └── deploy.py                        ← Referenz-Bereitstellung (UI-Pfad bevorzugt)
```

## Lizenz und Kontakt

Veröffentlicht unter der [MIT-Lizenz](LICENSE).

Pull Requests, Issues und Anfragen von Personalverantwortlichen oder
Recruitern sind willkommen — gerne über
[GitHub Issues](https://github.com/kiSchlag/ki-forderungsmanagement-agent/issues).
