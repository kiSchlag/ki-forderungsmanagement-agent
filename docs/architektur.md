# Architektur

Dieses Dokument beschreibt die Datenflüsse und Microsoft-365-Integration des
Müller-AR-Agents. Es ergänzt die [Szenarien](szenarien.md), den
[Werkzeugkatalog](werkzeuge.md) und die [Anweisungen](instruktionen.md).

## Komponenten und ihr Zusammenspiel

```
                            ┌──────────────────────────────────────┐
                            │           Microsoft 365              │
                            │   Outlook · Teams · SharePoint       │
                            │   Power BI · Entra ID                │
                            └──────────┬───────────────────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │      Copilot Studio         │
                        │  Anweisungen · Werkzeuge    │
                        │  Wissensquellen · Themen    │
                        │  Generative Orchestrierung  │
                        └──────────┬──────────────────┘
                                   │
                ┌──────────────────┼──────────────────────┐
                │                  │                      │
        ┌───────▼───────┐  ┌───────▼───────┐    ┌─────────▼────────┐
        │ Dataverse     │  │ Power Automate│    │ Externe Signale  │
        │ Wissensquelle │  │ Agent-Flows   │    │ Creditreform-API │
        │ (15 Tabellen) │  │ (28 Werkzeuge)│    │ Insolvenzhinweise│
        └───────────────┘  └───────┬───────┘    └──────────────────┘
                                   │
                          ┌────────▼────────┐
                          │   Dataverse     │
                          │  Schreibzugriff │
                          │  (Audit, Plan,  │
                          │   Sperre, Mahn) │
                          └─────────────────┘
```

## Beispiel-Datenfluss: eingehende Kunden-E-Mail

Eine typische Anfrage durchläuft folgende Stationen:

1. **Outlook**: Kunde sendet Mail an `forderungen@mueller-industrie.de`.
2. **Power Automate** (Mailbox-Trigger): Flow stößt den Agent an.
3. **Copilot Studio**: Generative Orchestrierung wählt
   - das Kompositions-Werkzeug **T27 `classify_inbound_email`** zur
     Klassifikation,
   - bei einer Stundungsanfrage anschließend
     **T3 `get_customer_payment_baseline`** und
     **T7 `get_customer_ltv`** zur Bewertung der Kundenbeziehung,
   - und schließlich entweder das deterministische Thema
     **`PlaceOrderHold`** (bei Risiko) oder
     **T17 `log_agent_decision`** + **T26 `draft_dunning_email`**
     (bei standardisierter Antwort).
4. **Dataverse**: Schreibzugriff via Agent-Flow auf
   `mueller_agentdecision`, `mueller_dunningevent` o. ä.
5. **Outlook**: Antwort wird (bei niedriger Konfidenz im Entwurf) an
   einen Menschen zur Freigabe weitergeleitet, bei hoher Konfidenz direkt
   versendet.

## Microsoft 365 Berührungspunkte

| Dienst       | Rolle im Agent                                                                                  |
|--------------|--------------------------------------------------------------------------------------------------|
| **Outlook**  | Lese- und Sendekanal für die AR-Mailbox. Eingehende E-Mails sind Auslöser, ausgehende Antworten Ergebnis. |
| **Teams**    | Eskalationskanal für Vertrieb (z. B. Auftragssperre mit One-Click-Override). Auch Empfänger der Morgen-Briefing-Karten. |
| **SharePoint** | Speicherort für Lieferscheine und signierte Lieferbestätigungen. **T16 `find_delivery_note_for_invoice`** liefert die Direkt-URL. |
| **Power BI** | Live-Visualisierung der DSO-, Aging- und Cash-Forecast-Daten. Das tägliche CFO-Briefing referenziert konkrete Visuals. |
| **Entra ID** | Authentifizierung der Skripte über vertrauliche Client-App; Application User in der Power-Platform-Admin-Konsole. |

## Schemaprovisionierung als Code

Die elf kundenspezifischen Müller-Tabellen und über 100 zusätzlichen
Spalten werden **nicht** im Maker händisch angelegt, sondern aus der
versionierten [`scenario_table_shortlist.json`](../data/scenario_table_shortlist.json)
heraus über die Web-API provisioniert. Die Vorteile:

- **Reproduzierbarkeit**: jeder neue Tenant erhält identisches Schema.
- **Idempotenz**: Vor jedem `POST` prüft das Skript per `GET` (mit
  `allow_404=True`), ob Tabelle / Spalte / Beziehung bereits existiert.
- **Versionierbarkeit**: Schema-Änderungen sind im Git-Verlauf sichtbar.

Aufruf:

```bash
python -m ki_forderungsmanagement.provision_schema           # Probe-Lauf
python -m ki_forderungsmanagement.provision_schema --apply   # Anwenden
```

## Synthetische Daten als deterministisches Fundament

Demo-Daten werden mit `numpy.random.default_rng(seed=42)` erzeugt — der
Lauf produziert byte-identische Ergebnisse:

- 3.000 fiktive Kunden mit deutschen Namen, PLZ und USt-IdNr
- 31.309 Rechnungen über 24 Monate
- 22.580 fiktive Kunden-E-Mails als Aktivitätsdaten
- 15 fest verdrahtete *Story-Patterns* (z. B. Insolvenz-Kandidat,
  loyaler Stammkunde mit Skontoabzug, Wiederholungstäter mit Ratenplan)

Eine Verifikationsroutine prüft vor jedem `--apply`-Lauf, dass alle
15 Story-Patterns reproduziert werden. Nur bei vollständiger
Verifikation wird der Lauf ausgeführt.

## Sicherheits- und Datenschutzmodell

| Bereich               | Maßnahme                                                                       |
|-----------------------|--------------------------------------------------------------------------------|
| **Authentifizierung** | OAuth-2.0-Client-Credentials gegen Microsoft Entra ID via MSAL                |
| **Geheimnisverwaltung** | Ausschließlich über `.env` (per `.gitignore` ausgeschlossen)                 |
| **Drosselung**         | `Retry-After`-konform, exponentielles Backoff mit Jitter auf 5xx + Netzwerkfehlern |
| **Audit-Trail**        | Append-only-Tabelle `mueller_agentdecision`; jede KI-Entscheidung mit Konfidenz, Modellname, Zeitstempel |
| **GDPR**               | Auskunfts- und Löschanfragen über Werkzeug-Routing an Datenschutzbeauftragten  |
| **Anweisungs-Override** | Strategische Großkunden und insolvenzgefährdete Kunden werden zwingend an Menschen weitergeleitet |
