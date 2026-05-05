# Geschäftsszenarien

Dieses Dokument beschreibt die zehn Geschäftsszenarien, die der KI-Agent für
das Forderungsmanagement der **Müller Industriebedarf GmbH** abdecken soll.
Jedes Szenario enthält eine kurze Motivation, fünf konkrete Anforderungen und
einen Verweis auf die zugehörigen Dataverse-Datenobjekte.

## Unternehmensprofil

| Kennzahl                     | Wert                                                                          |
|------------------------------|-------------------------------------------------------------------------------|
| **Branche**                  | B2B-Distributor für industrielle Software- und Hardwareprodukte               |
| **Hardware-Sortiment**       | Industriesensoren, Steuerungen, Netzwerk-Equipment, Sicherheitstechnik, Befestigungen, Handwerkzeuge, Messinstrumente |
| **Software-Sortiment**       | SCADA-Lizenzen, Predictive-Maintenance-Lösungen, ERP-Add-ons, OT-Cybersecurity, jährliche Wartungsverträge |
| **Kundenstamm**              | 2.400 aktive Geschäftskunden im DACH-Raum (Hersteller, Werkstätten, Bauunternehmen, kleinere Distributoren) |
| **DSO (Days Sales Outstanding)** | 47 Tage — Branchen-Benchmark: 32 Tage. Diese Lücke zu schließen, ist die Aufgabe des Agents. |

---

## Szenario 1 — Vorausschauende Zahlungsausfallerkennung

Der Agent prüft jede offene Rechnung und prognostiziert Verzögerungen,
**bevor** sie überfällig wird, sodass das Team frühzeitig handeln kann
statt nachträglich zu reagieren.

1. Tägliche Risikobewertung aller offenen Rechnungen anhand der Zahlungshistorie
2. Markierung von Rechnungen, die 14 Tage vor Fälligkeit eine Hochrisiko-Schwelle überschreiten
3. Erkennung von Kunden, deren Zahlungsverhalten von ihrem persönlichen Basiswert abweicht
4. Saisonal bedingte Zahlungsrisiken erkennen (z. B. Q1-Liquiditätsengpässe, Sommerflaute)
5. Risikobewertung mit externen Signalen abgleichen (Creditreform-Herabstufungen, Insolvenzhinweise)

**Datenmodellbezug**: `invoice`, `mueller_riskscoresnapshot`, `mueller_creditsignal`

---

## Szenario 2 — Kundengruppen-spezifische Kommunikation

Der Agent versendet **nicht** denselben Standard-Mahnbrief an jeden Kunden.
Er segmentiert nach Loyalität und Verhalten und formuliert die richtige
Botschaft im richtigen Ton.

1. Freundliche Erinnerung für loyale Stammkunden mit One-Click-Stundungsoption
2. Standardmahnung für stabile Kunden des mittleren Segments
3. Eskalierender Ton mit fortschreitender Dringlichkeit für Wiederholungstäter
4. Strenge Bestellstornierungs-Warnung für Neukunden, die ihre Erstrechnung nicht bezahlt haben
5. White-Glove-Behandlung mit menschlicher Freigabe für strategische Schlüsselkunden

**Datenmodellbezug**: `account`, `contact`, `mueller_communicationpreference`,
`mueller_lifetimevaluesnapshot`

---

## Szenario 3 — Eingehende E-Mails verstehen und beantworten

Der Agent überwacht das gemeinsame AR-Postfach, versteht den Inhalt jeder
deutschen Kunden-E-Mail und antwortet entweder direkt oder leitet mit
vorbereitetem Kontext an einen Menschen weiter.

1. Klassifikation eingehender E-Mails: Stundungsanfrage, Reklamation, Liefernachweis, Zahlungsbestätigung, allgemeine Anfrage
2. Automatische Antwort auf einfache Fälle (z. B. Liefernachweis aus SharePoint anhängen)
3. Entwurf deutschsprachiger Antworten zur menschlichen Freigabe in komplexeren Fällen
4. Erkennung von Frustration oder Eskalations-Sprachmustern, sofortige Weiterleitung an erfahrene Sachbearbeiter*innen
5. Protokollierung jeder E-Mail-Interaktion am Kundendatensatz

**Datenmodellbezug**: `email`, `incident`, `mueller_agentdecision`

---

## Szenario 4 — Stundungen und Ratenpläne automatisieren

Wenn ein Kunde nicht fristgerecht zahlen kann, kann der Agent innerhalb
vorab definierter Regeln eine strukturierte Stundungs- oder Ratenplanlösung
**ohne menschliches Zutun** anbieten.

1. Automatische 14- oder 30-tägige Verlängerungen für berechtigte Stammkunden
2. Mehrstufige Ratenpläne für höhere Außenstände
3. Anpassung der Zahlungsbedingungen in Dynamics 365 Finance nach Kundenakzeptanz
4. Bestätigungs-E-Mails mit neuen Fälligkeitsterminen und ggf. SEPA-Mandat-Aktualisierungen
5. Compliance-Tracking; erneute Eskalation, wenn der Kunde den neuen Plan verfehlt

**Datenmodellbezug**: `mueller_paymentplan`, `mueller_paymentplaninstallment`,
`mueller_sepamandate`

---

## Szenario 5 — Auftragssperre und -freigabe

Der Agent verhindert die Auslieferung neuer Ware an Kunden mit offenen
Verbindlichkeiten — ohne dabei Kunden in gutem Stand auszubremsen.

1. Automatische Kreditsperre, sobald Kundensaldo das Limit überschreitet
2. Benachrichtigung der zuständigen Vertriebsbeauftragten via Teams mit Override-Option für strategische Fälle
3. Automatische Freigabe gehaltener Aufträge, sobald die Zahlung verbucht ist
4. Vollständige Sperre für Kunden in fortgeschrittener Beitreibung oder Insolvenz
5. Tagesreport aller gehaltenen Aufträge mit finanzieller Auswirkung

**Datenmodellbezug**: `salesorder`, `account`, `mueller_paymentevent`

---

## Szenario 6 — Reklamationen und Streitfälle bearbeiten

Ein nennenswerter Anteil verspäteter Zahlungen entsteht **nicht** durch
fehlende Liquidität, sondern durch Reklamationen. Der Agent unterstützt
schnelle Klärung, damit diese Fälle nicht still in den Forderungsausfall
laufen.

1. Erkennung aus E-Mail-Inhalten, dass eine Rechnung beanstandet wird
2. Bündelung von Lieferscheinen, signierten Lieferbestätigungen und ursprünglichen Bestellungen in ein Dossier
3. Routing an die richtige interne Stelle — Vertrieb, Logistik oder Finanzbuchhaltung
4. Bearbeitungsdauer messen; stagnierende Fälle eskalieren
5. Automatische Gutschriftsentwürfe nach menschlicher Freigabe

**Datenmodellbezug**: `incident`, `mueller_deliverynote`, `salesorderdetail`,
`invoice`

---

## Szenario 7 — CFO- und Management-Reporting

Das Management benötigt das Gesamtbild ohne fünf verschiedene Systeme
öffnen zu müssen. Der Agent erzeugt jeden Werktag eine saubere
Morgen-Briefing-E-Mail mit den relevanten Kennzahlen.

1. Tägliches Power-BI-Dashboard mit Live-DSO, „Bezahlt heute" und Aging-Buckets
2. Wöchentliches Outlook-Briefing an den CFO mit risikobewerteten AR-Aging-Ständen pro Segment
3. Adaptive Teams-Karte an den AR-Teamleiter jeden Morgen mit den drei wichtigsten Kunden
4. Zahlungsprognose zum Monatsende mit Konfidenzintervall
5. Anomalie-Alarme bei DSO- oder Außenstandsabweichungen vom Basiswert

**Datenmodellbezug**: `invoice`, `mueller_paymentevent`, `mueller_riskscoresnapshot`

---

## Szenario 8 — Kundenwert und Abwanderungsschutz

Aggressive Beitreibung kann eine einzelne Rechnung gewinnen und einen
zehnjährigen Stammkunden verlieren. Der Agent wägt Liquiditätsgewinn
gegen Beziehungswert ab.

1. Customer Lifetime Value vor jeder Kommunikation berechnen
2. Automatische Mahnungen für strategische Kunden unterdrücken; Routing an Account-Manager
3. Erkennung sinkender Bestellfrequenz als Frühwarnsignal für Abwanderung
4. Account-Manager-Outreach auslösen, wenn Beitreibung das Verhältnis gefährdet
5. Verhalten nach Beitreibung tracken, um den Beziehungs-Effekt der Agentenentscheidungen zu messen

**Datenmodellbezug**: `mueller_lifetimevaluesnapshot`, `mueller_churnsignal`,
`mueller_agentdecision`

---

## Szenario 9 — Produkt- und vertragsspezifische Logik

Software-Verträge, Hardware-Lieferungen und wiederkehrender Support haben
unterschiedliche Abrechnungsrhythmen und Regeln. Der Agent kennt den
Unterschied.

1. Erkennung jährlicher Software-Lizenzverlängerungen, Auslösung des Renewal-Workflows 90 Tage im Voraus
2. Meilensteinabrechnung für Hardware-Systeminstallationen
3. Wiederkehrende Wartungsverträge mit Auto-Renewal-Logik
4. Erkennung abgelaufener Verträge, die noch berechnet werden — Markierung als Buchungsfehler
5. Hardware-/Software-Cross-Selling auf Basis von Nutzungssignalen

**Datenmodellbezug**: `contract`, `contractdetail`, `product`

---

## Szenario 10 — Compliance und Audit-Trail

Jede Aktion des Agents muss gegenüber Wirtschaftsprüfung **und** GDPR
nachweisbar sein. Der Agent handelt nicht im Verborgenen.

1. Protokollierung jeder Entscheidung mit Begründung, Konfidenzwert und Modellname
2. Bearbeitung von GDPR-Auskunfts- und Löschanfragen
3. Erstellung eines vollständigen Audit-Trails der Agent-Kommunikation pro Kunde auf Anfrage
4. Berücksichtigung der Kommunikationspräferenzen (formelles „Sie" vs. informelles „du", Anrede)
5. Möglichkeit für Menschen, jede Agent-Entscheidung zu überschreiben — und langfristig daraus zu lernen

**Datenmodellbezug**: `mueller_agentdecision`, `mueller_communicationpreference`,
alle Aktivitätstabellen
