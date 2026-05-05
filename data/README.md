# Datenverzeichnis

Dieses Verzeichnis enthält versionierte Konfigurations- und Schemadateien des
Projekts. Generierte Daten (Schema-Dumps, synthetische Datensätze) gehören
**nicht** in das Repository und sind über `.gitignore` ausgeschlossen.

## Versioniert (in Git)

| Datei                              | Zweck                                                                                          |
|------------------------------------|------------------------------------------------------------------------------------------------|
| `scenario_table_shortlist.json`    | Kuratierte Schema-Definition: standardisierte Dataverse-Tabellen, kundenspezifische Tabellen, kundenspezifische Spalten. Wird von `provision_schema.py` als Quelle verwendet. |

## Nicht versioniert (lokal, regenerierbar)

| Datei / Verzeichnis                | Wie wiederhergestellt                                                          |
|------------------------------------|--------------------------------------------------------------------------------|
| `output/dataverse_metadata.json`   | `python -m ki_forderungsmanagement.export_metadata`                            |
| `output/dataverse_metadata.partial.json` | Resumable-Checkpoint des obigen Skripts                                  |
| `output/synthetic_data_preview/`   | `python -m ki_forderungsmanagement.generate_synthetic_data --seed 42`          |
| `output/synthetic_id_map.json`     | Wird beim Apply-Lauf der synthetischen Daten geschrieben                       |
| `output/synthetic_data_progress.json` | Generator-Checkpoint                                                       |

## Begründung

Die generierten Datensätze umfassen ca. 280 MB und enthalten zwar nur
synthetische, aber realitätsnahe Geschäftsdaten (3.000 fiktive Kunden,
über 31.000 Rechnungen). Da der Agent hier deterministisch arbeitet
(`Seed 42`, NumPy + Faker), reproduziert ein erneuter Lauf die Daten
**byte-identisch** — eine Versionierung im Repository ist daher unnötig
und würde den Klon-Aufwand für interessierte Leser erheblich erhöhen.
