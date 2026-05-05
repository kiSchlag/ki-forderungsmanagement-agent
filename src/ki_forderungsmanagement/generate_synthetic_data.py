"""Generate deterministic synthetic German B2B AR data for the Müller demo.

Generates customers, contacts, addresses, sales orders, invoices, payment
events, dunning events, risk-score snapshots, communication preferences,
SEPA mandates, and 15 hand-curated story-pattern customers. Data is fully
deterministic given a seed, so the same command always produces byte-
identical preview JSON.

Default mode is dry-run: data is written to ``paths.preview_dir`` (default
``<repo>/output/synthetic_data_preview``). Pass ``--apply`` to actually
``POST`` rows to Dataverse via ``$batch``; ``--apply`` with > 100,000 rows
also requires ``--confirm-large``.

Sources (Microsoft Learn):
  - Create rows: https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/create-entity-web-api
  - $batch:      https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api
  - Activities:  https://learn.microsoft.com/en-us/power-apps/developer/data-platform/activity-entities
  - API limits:  https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits

Usage::

    python -m ki_forderungsmanagement.generate_synthetic_data                    # dry-run, 3000 customers
    python -m ki_forderungsmanagement.generate_synthetic_data --customers 100   # smaller dry-run
    python -m ki_forderungsmanagement.generate_synthetic_data --apply --confirm-large
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterable

import numpy as np
from faker import Faker

from .config import load_config
from .http_client import DataverseClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 1033

DEFAULT_CUSTOMERS = 3000
DEFAULT_MONTHS = 24
DEFAULT_SEED = 42
DEFAULT_BATCH_SIZE = 100
LARGE_THRESHOLD = 100_000

PREFIX = "mueller"  # publisher customization prefix used during provisioning


# ---------------------------------------------------------------------------
# German data tables (curated, embedded inline so the script is self-contained)
# ---------------------------------------------------------------------------

# 50 German PLZ <-> city <-> Bundesland triples covering all 16 Bundeslaender.
GERMAN_LOCATIONS = [
    ("80331", "Muenchen", "Bayern"), ("80539", "Muenchen", "Bayern"),
    ("90402", "Nuernberg", "Bayern"), ("86150", "Augsburg", "Bayern"),
    ("93047", "Regensburg", "Bayern"),
    ("70173", "Stuttgart", "Baden-Wuerttemberg"), ("76131", "Karlsruhe", "Baden-Wuerttemberg"),
    ("79098", "Freiburg im Breisgau", "Baden-Wuerttemberg"), ("68159", "Mannheim", "Baden-Wuerttemberg"),
    ("89073", "Ulm", "Baden-Wuerttemberg"),
    ("10115", "Berlin", "Berlin"), ("10785", "Berlin", "Berlin"),
    ("12435", "Berlin", "Berlin"), ("13355", "Berlin", "Berlin"),
    ("14467", "Potsdam", "Brandenburg"), ("03046", "Cottbus", "Brandenburg"),
    ("28195", "Bremen", "Bremen"), ("27568", "Bremerhaven", "Bremen"),
    ("20095", "Hamburg", "Hamburg"), ("22767", "Hamburg", "Hamburg"),
    ("21073", "Hamburg", "Hamburg"),
    ("60311", "Frankfurt am Main", "Hessen"), ("65183", "Wiesbaden", "Hessen"),
    ("64283", "Darmstadt", "Hessen"), ("34117", "Kassel", "Hessen"),
    ("19053", "Schwerin", "Mecklenburg-Vorpommern"), ("18055", "Rostock", "Mecklenburg-Vorpommern"),
    ("30159", "Hannover", "Niedersachsen"), ("38100", "Braunschweig", "Niedersachsen"),
    ("26122", "Oldenburg", "Niedersachsen"), ("49074", "Osnabrueck", "Niedersachsen"),
    ("50667", "Koeln", "Nordrhein-Westfalen"), ("40213", "Duesseldorf", "Nordrhein-Westfalen"),
    ("44135", "Dortmund", "Nordrhein-Westfalen"), ("45127", "Essen", "Nordrhein-Westfalen"),
    ("47051", "Duisburg", "Nordrhein-Westfalen"), ("33602", "Bielefeld", "Nordrhein-Westfalen"),
    ("48143", "Muenster", "Nordrhein-Westfalen"),
    ("55116", "Mainz", "Rheinland-Pfalz"), ("67059", "Ludwigshafen am Rhein", "Rheinland-Pfalz"),
    ("56068", "Koblenz", "Rheinland-Pfalz"),
    ("66111", "Saarbruecken", "Saarland"),
    ("01067", "Dresden", "Sachsen"), ("04109", "Leipzig", "Sachsen"),
    ("09111", "Chemnitz", "Sachsen"),
    ("39104", "Magdeburg", "Sachsen-Anhalt"), ("06108", "Halle (Saale)", "Sachsen-Anhalt"),
    ("24103", "Kiel", "Schleswig-Holstein"), ("23552", "Luebeck", "Schleswig-Holstein"),
    ("99084", "Erfurt", "Thueringen"), ("07743", "Jena", "Thueringen"),
]

# Amtsgerichte commonly listed for Handelsregister.
AMTSGERICHTE = [
    "Amtsgericht Muenchen", "Amtsgericht Hamburg", "Amtsgericht Berlin-Charlottenburg",
    "Amtsgericht Frankfurt am Main", "Amtsgericht Stuttgart", "Amtsgericht Koeln",
    "Amtsgericht Duesseldorf", "Amtsgericht Hannover", "Amtsgericht Bremen",
    "Amtsgericht Leipzig", "Amtsgericht Nuernberg", "Amtsgericht Mannheim",
    "Amtsgericht Dortmund", "Amtsgericht Essen", "Amtsgericht Bielefeld",
]

# German bank BLZ + BIC samples (8-digit BLZ + plausible BIC).
GERMAN_BANKS = [
    ("70010080", "PBNKDEFFXXX", "Postbank Muenchen"),
    ("60020030", "HYVEDEMMXXX", "UniCredit Bank Stuttgart"),
    ("10000000", "MARKDEF1100", "Deutsche Bundesbank Berlin"),
    ("50010517", "INGDDEFFXXX", "ING-DiBa Frankfurt"),
    ("76050101", "SSKNDE77XXX", "Sparkasse Nuernberg"),
    ("30050000", "DUSSDEDDXXX", "Landesbank Hessen-Thueringen"),
    ("20050550", "HASPDEHHXXX", "Hamburger Sparkasse"),
    ("60050101", "SOLADEST600", "Baden-Wuerttembergische Bank Stuttgart"),
    ("66060000", "GENODE61SLS", "Volksbank Saar"),
    ("85050300", "OSDDDE81XXX", "Ostdeutsche Sparkasse Dresden"),
]

# Industry buckets for company-name generation.
INDUSTRIES = [
    ("Maschinenbau", "Maschinenbau"), ("Logistik", "Logistik"),
    ("Chemie", "Chemie"), ("Bau", "Bau"),
    ("Werkzeuge", "Werkzeugtechnik"), ("Sicherheitstechnik", "Sicherheitstechnik"),
    ("Stahlhandel", "Stahlhandel"), ("Praezisionstechnik", "Praezisionstechnik"),
    ("Industriebedarf", "Industriebedarf"), ("Elektrotechnik", "Elektrotechnik"),
    ("Anlagenbau", "Anlagenbau"), ("Werkzeughandel", "Werkzeughandel"),
]

# German legal forms with weights summing to 100%.
LEGAL_FORMS = [
    ("GmbH", 60), ("GmbH & Co. KG", 15), ("AG", 8), ("KG", 5),
    ("OHG", 4), ("GbR", 3), ("UG (haftungsbeschraenkt)", 3),
    ("Einzelunternehmen", 2),
]

# Common German first names + surnames.
GERMAN_FIRST_NAMES = [
    "Andreas", "Stefan", "Michael", "Thomas", "Markus", "Christian", "Peter", "Frank",
    "Wolfgang", "Klaus", "Juergen", "Bernd", "Werner", "Hans", "Helmut", "Dieter",
    "Sabine", "Petra", "Andrea", "Claudia", "Birgit", "Monika", "Sandra", "Stefanie",
    "Anna", "Maria", "Susanne", "Karin", "Christine", "Brigitte", "Renate", "Ursula",
    "Tobias", "Florian", "Daniel", "Martin", "Patrick", "Sebastian", "Lukas", "Jan",
    "Julia", "Lena", "Laura", "Katharina", "Sarah", "Nadine", "Vanessa", "Melanie",
]

GERMAN_SURNAMES = [
    "Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner",
    "Becker", "Schulz", "Hoffmann", "Schaefer", "Koch", "Bauer", "Richter",
    "Klein", "Wolf", "Schroeder", "Neumann", "Schwarz", "Zimmermann", "Braun",
    "Krueger", "Hofmann", "Hartmann", "Lange", "Schmitt", "Werner", "Schmitz",
    "Krause", "Lehmann", "Koehler", "Herrmann", "Walter", "Mayer", "Huber",
    "Kaiser", "Fuchs", "Peters", "Lang", "Scholz", "Moeller", "Weiss", "Jung",
    "Hahn", "Schubert", "Vogel", "Friedrich", "Keller", "Guenther", "Frank",
    "Berger", "Winkler", "Roth", "Beck", "Lorenz", "Baumann", "Franke",
]

# Hardware product templates: (sku_family, name_template, base_price_min, max).
HARDWARE_TEMPLATES = [
    ("SENS-TEMP", "Industrie-Temperatursensor PT100 Typ {n}", 89, 480),
    ("SENS-PRES", "Drucksensor 0-{n} bar IP67", 145, 1200),
    ("SENS-FLOW", "Magnetisch-induktiver Durchflussmesser DN{n}", 690, 4500),
    ("CTRL-PLC", "Kompakt-SPS Modul {n} (16 Ein-/Ausgaenge)", 320, 2400),
    ("NET-SW", "Industrie-Switch {n}-Port (verwaltet, IP30)", 410, 2100),
    ("FAST-SCR", "Edelstahlschraube DIN 933 M{n}x60 (VPE 100)", 28, 95),
    ("PSA-HELM", "Schutzhelm Schicht-{n} mit Visier", 42, 180),
    ("TOOL-MES", "Digitales Praezisions-Messgeraet {n}", 380, 1800),
    ("VLV-BALL", "Edelstahl-Kugelhahn DN{n} mit Pneumatikantrieb", 220, 1500),
    ("CAB-PWR", "Industriekabelsatz {n}m H07RN-F", 95, 480),
]

# Software / service product templates.
SOFTWARE_TEMPLATES = [
    ("LIC-SCADA", "SCADA-Lizenz {n} Tags (1 Jahr)", 1800, 24000),
    ("LIC-PRED", "Predictive Maintenance Suite Modul {n}", 4800, 48000),
    ("LIC-CYBER", "OT-Cybersecurity Pack {n} Endpunkte", 2200, 19000),
    ("SUPP-ANN", "Support-Vertrag Premium ({n} Stunden / Jahr)", 3200, 28000),
    ("SUPP-MAINT", "Wartungsvertrag Standard {n}-Jahre", 2800, 18000),
    ("LIC-ERP", "ERP-Add-on {n} (Schnittstellenpaket)", 6500, 32000),
]

# German email subject + body templates per email_classification.
EMAIL_TEMPLATES: Dict[str, List[Tuple[str, str]]] = {
    "Reschedule": [
        ("Bitte um Zahlungsaufschub Rechnung {invoice_number}",
         "{anrede},\n\naufgrund einer voruebergehenden Liquiditaetslage bitten wir um eine Zahlungsfrist-Verlaengerung um 14 Tage fuer Rechnung {invoice_number} (Betrag: {amount_eur} EUR, urspruengliche Faelligkeit: {due_date}).\n\nMit freundlichen Gruessen\n{customer_name}"),
        ("Verlaengerung Zahlungsziel {invoice_number}",
         "{anrede},\n\nwir bitten um Aufschub bis Monatsende fuer die o.g. Rechnung. Die Zahlung erfolgt dann in voller Hoehe.\n\nVielen Dank im Voraus,\n{customer_name}"),
        ("Anfrage Ratenzahlung Rechnung {invoice_number}",
         "{anrede},\n\nkoennten wir den Rechnungsbetrag von {amount_eur} EUR in 3 Monatsraten begleichen? Vielen Dank fuer Ihr Verstaendnis.\n\n{customer_name}"),
    ],
    "Dispute": [
        ("Reklamation zu Rechnung {invoice_number}",
         "{anrede},\n\nbei der Pruefung der Rechnung {invoice_number} ist uns ein Preisunterschied zu unserem Auftrag aufgefallen. Wir bitten um Pruefung und ggf. um Korrektur.\n\nMit freundlichen Gruessen,\n{customer_name}"),
        ("Mengen-Differenz Lieferung {invoice_number}",
         "{anrede},\n\ndie gelieferte Stueckzahl entspricht nicht der Bestellung (siehe Lieferschein). Wir bitten um Klaerung vor Begleichung.\n\n{customer_name}"),
        ("Beanstandung Warensendung Rechnung {invoice_number}",
         "{anrede},\n\nein Teil der Lieferung war beschaedigt. Wir behalten den fraglichen Teilbetrag (siehe Anlage) zurueck und bitten um Stellungnahme.\n\n{customer_name}"),
    ],
    "ProofOfDelivery": [
        ("Bitte um Lieferschein zu Rechnung {invoice_number}",
         "{anrede},\n\nfuer unsere interne Buchhaltung benoetigen wir eine Kopie des unterzeichneten Lieferscheins zur Rechnung {invoice_number}.\n\nVielen Dank,\n{customer_name}"),
        ("POD-Anfrage {invoice_number}",
         "{anrede},\n\nkoennen Sie uns bitte den Proof-of-Delivery zusenden? Anlage zur Rechnung {invoice_number}.\n\n{customer_name}"),
    ],
    "PaymentConfirmation": [
        ("Zahlungsbestaetigung Rechnung {invoice_number}",
         "{anrede},\n\nwir bestaetigen den Geldeingang in Hoehe von {amount_eur} EUR auf Rechnung {invoice_number} per heutigem Datum.\n\nMit freundlichen Gruessen,\nBuchhaltung\n{customer_name}"),
        ("Zahlung erfolgt {invoice_number}",
         "{anrede},\n\ndie Ueberweisung wurde heute angewiesen (Wertstellung morgen).\n\n{customer_name}"),
    ],
    "GeneralQuestion": [
        ("Frage zu Konditionen Rechnung {invoice_number}",
         "{anrede},\n\nuns ist nicht klar, wie sich der Skonto-Anteil der Rechnung {invoice_number} berechnet. Koennen Sie das bitte erlaeutern?\n\n{customer_name}"),
        ("Anfrage Rahmenvertrag",
         "{anrede},\n\nwir interessieren uns fuer einen Rahmenvertrag mit Mengenstaffel ab Q3. Koennen wir einen Termin vereinbaren?\n\n{customer_name}"),
    ],
}

# German dunning letter templates per tone.
DUNNING_TEMPLATES: Dict[str, Tuple[str, str]] = {
    "Friendly": (
        "Freundliche Zahlungserinnerung Rechnung {invoice_number}",
        "{anrede},\n\nvielleicht ist es Ihrer Aufmerksamkeit entgangen: Die Rechnung {invoice_number} ueber {amount_eur} EUR vom {invoice_date} ist seit dem {due_date} faellig. Bitte ueberweisen Sie den Betrag in den naechsten Tagen oder melden Sie sich bei uns, falls etwas unklar ist.\n\nMit freundlichen Gruessen,\nMueller Industriebedarf GmbH\nBuchhaltung",
    ),
    "Standard": (
        "Mahnung zu Rechnung {invoice_number}",
        "{anrede},\n\ntrotz unserer freundlichen Zahlungserinnerung haben wir bisher keinen Zahlungseingang fuer Rechnung {invoice_number} ueber {amount_eur} EUR feststellen koennen. Wir bitten Sie hoeflich, den ausstehenden Betrag bis zum {new_deadline} zu begleichen.\n\nMit freundlichen Gruessen,\nMueller Industriebedarf GmbH",
    ),
    "Strict": (
        "Letzte Mahnung Rechnung {invoice_number}",
        "{anrede},\n\nleider muessen wir feststellen, dass die Rechnung {invoice_number} ueber {amount_eur} EUR weiterhin offen ist. Wir setzen Ihnen hiermit eine letzte Frist bis zum {new_deadline}. Bei weiterem Zahlungsverzug behalten wir uns gerichtliche Schritte und die Uebergabe an unser Inkasso-Buero vor.\n\nMueller Industriebedarf GmbH",
    ),
    "WhiteGlove": (
        "Persoenliche Nachricht zu offener Rechnung {invoice_number}",
        "{anrede},\n\nin meiner Funktion als Ihr Key Account Manager moechte ich Sie diskret darauf hinweisen, dass die Rechnung {invoice_number} ueber {amount_eur} EUR noch offen ist. Bitte lassen Sie uns kurz telefonieren, falls etwas zu klaeren ist - ich stehe Ihnen jederzeit zur Verfuegung.\n\nHerzliche Gruesse,\nIhre Mueller Industriebedarf GmbH",
    ),
}

# OptionSet value bases (used by provisioning script; values start at 727000000).
OPT_BASE = 727000000


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    name: str
    mix_pct: float
    days_to_pay_mu: float
    days_to_pay_sigma: float
    dispute_rate: float
    skonto_claim_rate: float
    segment_value: int          # OPT_BASE + offset for mueller_segment
    tone_value: int             # OPT_BASE + offset for mueller_communicationtone
    suppress_automation: bool = False
    is_strategic: bool = False
    insolve: bool = False
    new_deadbeat: bool = False
    cash_crunch_q1: bool = False
    drift: bool = False
    formality: str = "Sie"      # Sie / Du for Anrede
    payment_terms_code: int = 1


# segment values: Strategic=0, Loyal=1, MidTier=2, RepeatLatePayer=3, New=4
# tone values:    Friendly=0, Standard=1, Strict=2, WhiteGlove=3
PROFILES: List[Profile] = [
    Profile("Loyal-Skonto-Taker", 0.18,  9.0, 1.5, 0.00, 0.95, OPT_BASE + 1, OPT_BASE + 0),
    Profile("Standard-On-Time",   0.42, 27.0, 3.0, 0.01, 0.05, OPT_BASE + 2, OPT_BASE + 1),
    Profile("Slow-Drifter",       0.08, 28.0, 4.0, 0.03, 0.00, OPT_BASE + 2, OPT_BASE + 1, drift=True),
    Profile("Disputer",           0.06, 35.0, 8.0, 0.125, 0.00, OPT_BASE + 2, OPT_BASE + 1),
    Profile("Cash-Crunch-Q1",     0.07, 26.0, 6.0, 0.02, 0.05, OPT_BASE + 2, OPT_BASE + 1, cash_crunch_q1=True),
    Profile("Repeat-Late-Payer",  0.09, 50.0, 12.0, 0.04, 0.00, OPT_BASE + 3, OPT_BASE + 2),
    Profile("Strategic-Account",  0.04, 35.0, 5.0, 0.01, 0.00, OPT_BASE + 0, OPT_BASE + 3,
            suppress_automation=True, is_strategic=True),
    Profile("About-To-Insolve",   0.015,55.0, 15.0, 0.06, 0.00, OPT_BASE + 3, OPT_BASE + 2, insolve=True),
    Profile("New-Deadbeat",       0.045,90.0, 20.0, 0.00, 0.00, OPT_BASE + 4, OPT_BASE + 2, new_deadbeat=True),
]
assert abs(sum(p.mix_pct for p in PROFILES) - 1.0) < 0.001, "Profile mix must sum to 1.0"


# Story-pattern hero customer assignments.  These customers are placed at
# specific indices so the verification step can find them deterministically.
@dataclass
class StoryHero:
    index: int                    # customer index (0-based)
    company_name: str
    profile_name: str
    invoice_number_seed: str      # used to generate the headline invoice number
    description: str

STORY_HEROES: List[StoryHero] = [
    StoryHero(0,  "Schmidt Maschinenbau GmbH",          "Slow-Drifter",
              "INV-2026-1801", "drifted from 28d to 47d over recent quarter"),
    StoryHero(1,  "Weber Logistik AG",                  "About-To-Insolve",
              "INV-2026-1802", "Creditreform downgrade -42d, orders blocked"),
    StoryHero(2,  "Hoffmann Bau GmbH",                  "Cash-Crunch-Q1",
              "INV-2026-1803", "seasonal Q1 cash crunch"),
    StoryHero(3,  "Mueller Werkzeugtechnik GmbH",       "Strategic-Account",
              "INV-2026-1804", "4 overdue, automation suppressed"),
    StoryHero(4,  "Krueger Logistik GmbH",              "Disputer",
              "INV-2026-1847", "PriceMismatch dispute"),
    StoryHero(5,  "Lehmann Industrieanlagen GmbH",      "Repeat-Late-Payer",
              "INV-2025-9982", "Mahnstufe 3 escalation"),
    StoryHero(6,  "Becker Chemie AG",                   "Loyal-Skonto-Taker",
              "INV-2026-2103", "Skonto claimed on day 9"),
    StoryHero(7,  "Schwarz Logistik Sued GmbH",         "New-Deadbeat",
              "INV-2026-1500", "first invoice unpaid"),
    StoryHero(8,  "Wagner Stahlhandel AG",              "Standard-On-Time",
              "INV-2026-2244", "low-risk suppression confidence 0.94"),
    StoryHero(9,  "Fischer Praezisionstechnik GmbH",    "Loyal-Skonto-Taker",
              "INV-2026-2300", "renewal trigger -90d"),
    StoryHero(10, "Bauer Industriebedarf S.A.S.",       "Standard-On-Time",
              "INV-2026-0892", "EU reverse charge invoice"),
    StoryHero(11, "Hartmann Werkzeuge GmbH",            "Standard-On-Time",
              "INV-2026-1700", "partial payment 5400 of 12000"),
    StoryHero(12, "Klein Sicherheitstechnik GmbH",      "Strategic-Account",
              "INV-2026-1850", "personalized Mahnung Sehr geehrter Herr Klein"),
    StoryHero(13, "Neumann GmbH",                       "About-To-Insolve",
              "INV-2026-1851", "blockorders=true after 2026-04-15 signal"),
    StoryHero(14, "Schaefer Maintenance Solutions GmbH","Repeat-Late-Payer",
              "INV-2026-1455", "4-installment payment plan"),
]


# ---------------------------------------------------------------------------
# State management: id_map + progress
# ---------------------------------------------------------------------------

class State:
    def __init__(self, id_map_path: Path, progress_path: Path):
        self.id_map_path = id_map_path
        self.progress_path = progress_path
        self.id_map: Dict[str, str] = {}
        self.completed_tables: List[str] = []
        if id_map_path.is_file():
            self.id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
        if progress_path.is_file():
            self.completed_tables = json.loads(progress_path.read_text(encoding="utf-8")).get(
                "completed_tables", [])

    def synthetic_to_real(self, synthetic_id: str) -> Optional[str]:
        return self.id_map.get(synthetic_id)

    def record(self, synthetic_id: str, real_guid: str) -> None:
        self.id_map[synthetic_id] = real_guid

    def save_id_map(self) -> None:
        tmp = self.id_map_path.with_suffix(self.id_map_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.id_map, indent=2), encoding="utf-8")
        os.replace(tmp, self.id_map_path)

    def mark_table_done(self, table: str) -> None:
        if table not in self.completed_tables:
            self.completed_tables.append(table)
        tmp = self.progress_path.with_suffix(self.progress_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"completed_tables": self.completed_tables}, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.progress_path)

    def is_done(self, table: str) -> bool:
        return table in self.completed_tables


# ---------------------------------------------------------------------------
# German data utilities (IBAN, USt-IdNr, Handelsregister, name)
# ---------------------------------------------------------------------------

def german_iban(rng: np.random.Generator) -> Tuple[str, str, str]:
    """Return (iban, bic, bank_name) - synthetic but mod-97-valid IBAN."""
    blz, bic, name = GERMAN_BANKS[rng.integers(0, len(GERMAN_BANKS))]
    konto = "".join(str(int(rng.integers(0, 10))) for _ in range(10))
    bban = blz + konto
    # Mod-97 checksum: append "DE00" (numerical), compute mod 97, subtract from 98
    rearranged = bban + "131400"  # D=13, E=14, then 00
    check = 98 - (int(rearranged) % 97)
    iban = f"DE{check:02d}{bban}"
    return iban, bic, name


def german_ustidnr(rng: np.random.Generator) -> str:
    """Synthetic German USt-IdNr: DE + 9 digits with mod-11 check."""
    digits = [int(rng.integers(0, 10)) for _ in range(8)]
    # Compute checksum per UStG / BMF algorithm.  Standard mod-11 over 8 digits.
    p = 10
    for d in digits:
        s = (d + p) % 10 or 10
        p = (2 * s) % 11
    check = (11 - p) % 10
    return "DE" + "".join(str(d) for d in digits) + str(check)


def german_steuernummer(rng: np.random.Generator) -> str:
    """Synthetic Finanzamt Steuernummer in standard 5/3/4 format."""
    parts = [
        f"{int(rng.integers(10, 99)):02d}",
        f"{int(rng.integers(100, 999)):03d}",
        f"{int(rng.integers(10000, 99999)):05d}",
    ]
    return "/".join(parts)


def handelsregister_nummer(rng: np.random.Generator) -> str:
    prefix = "HRB" if rng.random() < 0.85 else "HRA"
    n = int(rng.integers(1000, 999999))
    return f"{prefix} {n}"


def german_company_name(rng: np.random.Generator, surname: str, industry_word: str,
                          city: str, legal: str) -> str:
    pattern = rng.integers(0, 5)
    if pattern == 0:
        base = f"{surname} {industry_word}"
    elif pattern == 1:
        base = f"{city} {industry_word}"
    elif pattern == 2:
        base = f"{surname} & Soehne {industry_word}"
    elif pattern == 3:
        base = f"{surname} {industry_word} {city}"
    else:
        base = f"{surname} & {GERMAN_SURNAMES[int(rng.integers(0, len(GERMAN_SURNAMES)))]} {industry_word}"
    return f"{base} {legal}"


def weighted_choice(rng: np.random.Generator, choices: List[Tuple[Any, float]]) -> Any:
    total = sum(w for _, w in choices)
    r = rng.random() * total
    acc = 0.0
    for v, w in choices:
        acc += w
        if r <= acc:
            return v
    return choices[-1][0]


def assign_profile(rng: np.random.Generator) -> Profile:
    """Profile assignment honoring the mix percentages."""
    return weighted_choice(rng, [(p, p.mix_pct) for p in PROFILES])


def short_uuid_from(prefix: str, *parts: Any) -> str:
    """Synthetic ID string: <prefix>:<index>.  Used as a key in the id_map."""
    return f"{prefix}:" + ":".join(str(p) for p in parts)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

@dataclass
class CustomerSpec:
    """In-memory representation of a generated customer (account)."""
    index: int
    synthetic_id: str
    profile: Profile
    name: str
    accountnumber: str
    legal_form: str
    handelsreg: str
    amtsgericht: str
    ustidnr: str
    steuernummer: str
    plz: str
    city: str
    bundesland: str
    street: str
    phone: str
    email: str
    iban: str
    bic: str
    bank_name: str
    primary_contact_synthetic: str
    creditlimit: float
    creditreform_score: float
    is_eu_non_de: bool = False  # for reverse-charge story


def generate_customers(rng: np.random.Generator, faker: Faker, n: int,
                        story_heroes: List[StoryHero]) -> List[CustomerSpec]:
    """Generate n customer specs.  First len(story_heroes) are pinned heroes."""
    hero_by_index = {h.index: h for h in story_heroes}
    customers: List[CustomerSpec] = []
    legal_choices = [(lf, w) for lf, w in LEGAL_FORMS]

    for i in range(n):
        synthetic_id = short_uuid_from("account", i)
        plz, city, bundesland = GERMAN_LOCATIONS[int(rng.integers(0, len(GERMAN_LOCATIONS)))]
        if i in hero_by_index:
            hero = hero_by_index[i]
            profile = next(p for p in PROFILES if p.name == hero.profile_name)
            company_name = hero.company_name
            # Extract legal form from the hero name's last token(s)
            legal = next((lf for lf, _ in LEGAL_FORMS if company_name.endswith(lf)), "GmbH")
        else:
            profile = assign_profile(rng)
            legal = weighted_choice(rng, legal_choices)
            surname = GERMAN_SURNAMES[int(rng.integers(0, len(GERMAN_SURNAMES)))]
            _, industry_word = INDUSTRIES[int(rng.integers(0, len(INDUSTRIES)))]
            company_name = german_company_name(rng, surname, industry_word, city, legal)

        # Hero #10 (Bauer Industriebedarf S.A.S.) is an EU non-DE entity
        is_eu = "S.A.S." in company_name or "S.A." in company_name

        accountnumber = f"MUE-{2024 + (i % 3)}-{i+1:05d}"
        iban, bic, bank = german_iban(rng)
        spec = CustomerSpec(
            index=i, synthetic_id=synthetic_id, profile=profile, name=company_name,
            accountnumber=accountnumber, legal_form=legal,
            handelsreg=handelsregister_nummer(rng),
            amtsgericht=AMTSGERICHTE[int(rng.integers(0, len(AMTSGERICHTE)))],
            ustidnr=german_ustidnr(rng) if not is_eu else "FR" + "".join(
                str(int(rng.integers(0, 10))) for _ in range(11)),
            steuernummer=german_steuernummer(rng),
            plz=plz, city=city, bundesland=bundesland,
            street=f"{faker.street_name()} {int(rng.integers(1, 200))}",
            phone=f"+49 {int(rng.integers(20, 99))} {int(rng.integers(1000000, 9999999))}",
            email=f"buchhaltung@{re.sub(r'[^a-z0-9]', '', company_name.lower().split()[0])}.de",
            iban=iban, bic=bic, bank_name=bank,
            primary_contact_synthetic=short_uuid_from("contact", i, 0),
            creditlimit=float(int(rng.integers(5_000, 250_000)) // 1000 * 1000),
            creditreform_score=float(round(rng.uniform(160, 320), 1))
                if profile.insolve else float(round(rng.uniform(220, 480), 1)),
            is_eu_non_de=is_eu,
        )
        customers.append(spec)
    return customers


# ---------------------------------------------------------------------------
# Build records (returns list of dicts ready for either preview JSON or POST)
# ---------------------------------------------------------------------------

def build_account_records(customers: List[CustomerSpec], today: date) -> List[dict]:
    out = []
    for c in customers:
        out.append({
            "_synthetic_id": c.synthetic_id,
            "name": c.name[:160],
            "accountnumber": c.accountnumber,
            "creditlimit": c.creditlimit,
            "creditonhold": False,
            "emailaddress1": c.email[:100],
            "telephone1": c.phone,
            "address1_line1": c.street,
            "address1_city": c.city,
            "address1_postalcode": c.plz,
            "address1_country": "Germany" if not c.is_eu_non_de else "France",
            "address1_stateorprovince": c.bundesland,
            f"{PREFIX}_legalform": OPT_BASE + ["GmbH","AG","KG","OHG","GbR","UG (haftungsbeschraenkt)","Einzelunternehmen","GmbH & Co. KG"].index(c.legal_form if c.legal_form != "Sonstige" else "GmbH"),
            f"{PREFIX}_handelsregisternummer": c.handelsreg,
            f"{PREFIX}_amtsgericht": c.amtsgericht,
            f"{PREFIX}_ustidnr": c.ustidnr,
            f"{PREFIX}_steuernummer": c.steuernummer,
            f"{PREFIX}_segment": c.profile.segment_value,
            f"{PREFIX}_communicationtone": c.profile.tone_value,
            f"{PREFIX}_isstrategicaccount": c.profile.is_strategic,
            f"{PREFIX}_blockorders": c.profile.insolve,  # insolve customers get blocked
            f"{PREFIX}_creditreformscore": c.creditreform_score,
            f"{PREFIX}_creditreformupdated": today.isoformat() + "T00:00:00Z",
            f"{PREFIX}_kontaktpreferenz_anrede": OPT_BASE + (0 if c.profile.formality == "Sie" else 1),
            f"{PREFIX}_dsodays": float(c.profile.days_to_pay_mu),
        })
    return out


def build_contact_records(customers: List[CustomerSpec], rng: np.random.Generator,
                            faker: Faker) -> List[dict]:
    out = []
    for c in customers:
        # Each customer gets 2-3 contacts (hero customers always exactly 3 to be predictable)
        n = 3 if c.index < len(STORY_HEROES) else int(rng.integers(2, 4))
        for k in range(n):
            first = GERMAN_FIRST_NAMES[int(rng.integers(0, len(GERMAN_FIRST_NAMES)))]
            last_pool = c.name.split()[0] if k == 0 else GERMAN_SURNAMES[int(rng.integers(0, len(GERMAN_SURNAMES)))]
            last = re.sub(r'[^A-Za-z]', '', last_pool) or "Kunde"
            out.append({
                "_synthetic_id": short_uuid_from("contact", c.index, k),
                "firstname": first[:50],
                "lastname": last[:50],
                "fullname": f"{first} {last}"[:160],
                "emailaddress1": f"{first.lower()}.{last.lower()}@kunde-{c.index}.de"[:100],
                "telephone1": f"+49 {int(rng.integers(20, 99))} {int(rng.integers(1000000, 9999999))}",
                "mobilephone": f"+49 17{int(rng.integers(0, 9))} {int(rng.integers(1000000, 9999999))}",
                "jobtitle": rng.choice(["Geschaeftsfuehrer", "Buchhaltung", "Einkauf",
                                          "Finanzleiter", "Assistenz GF", "Buchhalter"]),
                "_account_synthetic": c.synthetic_id,
                "parentcustomerid_account@odata.bind": f"_synthetic:{c.synthetic_id}",
            })
    return out


def build_address_records(customers: List[CustomerSpec], rng: np.random.Generator) -> List[dict]:
    """Build customeraddress records.

    Dataverse auto-creates two customeraddress rows (addressnumber=1 Bill To,
    addressnumber=2 Ship To) for every new account/contact. (parentid,
    addressnumber) is enforced as an alternate key, so any insert with
    addressnumber 1 or 2 collides and returns 0x80040237. We start at
    addressnumber=3 (which still embeds into account.address3_*) and
    increment per parent. Microsoft Learn ref:
    https://learn.microsoft.com/en-us/power-apps/developer/data-platform/customer-entities-account-contact
    """
    out = []
    for c in customers:
        next_num = 3  # 1 and 2 are reserved
        out.append({
            "_synthetic_id": short_uuid_from("customeraddress", c.index, 0),
            "name": "Rechnungsadresse",
            "addressnumber": next_num,
            "addresstypecode": 1,  # Bill To
            "line1": c.street,
            "city": c.city,
            "postalcode": c.plz,
            "stateorprovince": c.bundesland,
            "country": "Germany" if not c.is_eu_non_de else "France",
            "telephone1": c.phone,
            "parentid_account@odata.bind": f"_synthetic:{c.synthetic_id}",
        })
        next_num += 1
        # ~50% have a separate shipping address (addressnumber 4, standalone)
        if rng.random() < 0.5:
            plz, city, bundesland = GERMAN_LOCATIONS[int(rng.integers(0, len(GERMAN_LOCATIONS)))]
            out.append({
                "_synthetic_id": short_uuid_from("customeraddress", c.index, 1),
                "name": "Lieferadresse",
                "addressnumber": next_num,
                "addresstypecode": 2,  # Ship To
                "line1": f"Lager {int(rng.integers(1, 5))}",
                "city": city,
                "postalcode": plz,
                "stateorprovince": bundesland,
                "country": "Germany",
                "telephone1": c.phone,
                "parentid_account@odata.bind": f"_synthetic:{c.synthetic_id}",
            })
    return out


def build_communication_preference(customers: List[CustomerSpec]) -> List[dict]:
    out = []
    for c in customers:
        out.append({
            "_synthetic_id": short_uuid_from("communicationpreference", c.index),
            f"{PREFIX}_name": f"Praeferenzen {c.name[:80]}",
            f"{PREFIX}_formality": OPT_BASE + (0 if c.profile.formality == "Sie" else 1),
            f"{PREFIX}_preferredchannel": OPT_BASE + 0,  # Email
            f"{PREFIX}_suppressautomation": c.profile.suppress_automation,
            f"{PREFIX}_language": "de-DE",
            f"{PREFIX}_quiethoursstart": 18,
            f"{PREFIX}_quiethoursend": 8,
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
        })
    return out


def build_sepa_mandates(customers: List[CustomerSpec], rng: np.random.Generator,
                         today: date) -> List[dict]:
    out = []
    for c in customers:
        if rng.random() > 0.7:  # ~70% have an active mandate
            continue
        signed = today - timedelta(days=int(rng.integers(30, 730)))
        out.append({
            "_synthetic_id": short_uuid_from("sepamandate", c.index),
            f"{PREFIX}_name": f"SEPA Mandat {c.accountnumber}",
            f"{PREFIX}_mandatereference": f"MUE-MAN-{c.index:06d}",
            f"{PREFIX}_iban": c.iban,
            f"{PREFIX}_ibantail": c.iban[-4:],
            f"{PREFIX}_bic": c.bic,
            f"{PREFIX}_creditorid": "DE98ZZZ09999999999",
            f"{PREFIX}_mandatetype": OPT_BASE + (0 if rng.random() < 0.7 else 1),  # CORE / B2B
            f"{PREFIX}_firstuseflag": rng.random() < 0.1,
            f"{PREFIX}_amendmentcount": int(rng.integers(0, 3)),
            f"{PREFIX}_signeddate": signed.isoformat() + "T00:00:00Z",
            f"{PREFIX}_lastuseddate": (today - timedelta(days=int(rng.integers(0, 90)))).isoformat() + "T00:00:00Z",
            f"{PREFIX}_status": OPT_BASE + 0,  # Active
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
        })
    return out


def build_lifetime_value_snapshots(customers: List[CustomerSpec], today: date,
                                     rng: np.random.Generator) -> List[dict]:
    """Monthly LTV snapshot for the last 12 months.

    Note: account.mueller_segment has 5 options (Strategic / Loyal / MidTier /
    RepeatLatePayer / New) but mueller_lifetimevaluesnapshot.mueller_segment
    has only 4 options (Strategic / Loyal / MidTier / NewOrAtRisk) per the
    shortlist's purpose strings. We collapse RepeatLatePayer + New into
    NewOrAtRisk for the snapshot.
    """
    # Account segment value -> snapshot segment value.
    seg_map = {
        OPT_BASE + 0: OPT_BASE + 0,  # Strategic
        OPT_BASE + 1: OPT_BASE + 1,  # Loyal
        OPT_BASE + 2: OPT_BASE + 2,  # MidTier
        OPT_BASE + 3: OPT_BASE + 3,  # RepeatLatePayer -> NewOrAtRisk
        OPT_BASE + 4: OPT_BASE + 3,  # New             -> NewOrAtRisk
    }
    out = []
    for c in customers:
        snap_seg = seg_map.get(c.profile.segment_value, OPT_BASE + 3)
        for m in range(12):
            snap_date = today - timedelta(days=30 * m)
            ltv = float(round(rng.uniform(50_000, 800_000), 2))
            if c.profile.is_strategic:
                ltv *= 3.5
            elif c.profile.name == "Loyal-Skonto-Taker":
                ltv *= 1.6
            out.append({
                "_synthetic_id": short_uuid_from("ltv", c.index, m),
                f"{PREFIX}_name": f"LTV {c.accountnumber} {snap_date.isoformat()}",
                f"{PREFIX}_snapshotdate": snap_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_ltvamount": round(ltv, 2),
                f"{PREFIX}_revenuetotal": round(ltv * 1.25, 2),
                f"{PREFIX}_margintotal": round(ltv * 0.32, 2),
                f"{PREFIX}_tenuredays": int(rng.integers(180, 3000)),
                f"{PREFIX}_segment": snap_seg,
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
            })
    return out


def build_products(rng: np.random.Generator) -> List[dict]:
    """Build product specs WITHOUT defaultuom bindings.

    Microsoft Learn confirms every product requires DefaultUoMScheduleId and
    DefaultUoMId (system-required references). Those bindings are injected at
    apply-time by patch_products_with_uom_defaults() once we've fetched the
    environment's default Unit Group + Unit (they aren't deterministic, so we
    can't know the GUIDs at generation time). Source:
    https://learn.microsoft.com/en-us/dynamics365/sales/developer/create-manage-product-families-products-bundles-product-properties
    """
    out = []
    pid = 0
    for sku_family, name_template, p_min, p_max in HARDWARE_TEMPLATES:
        for _ in range(6):
            n = int(rng.integers(2, 200))
            sku = f"{sku_family}-{int(rng.integers(10000, 99999))}"
            out.append({
                "_synthetic_id": short_uuid_from("product", pid),
                "productnumber": sku,
                "name": name_template.format(n=n)[:100],
                "description": f"Hardware-Komponente {sku_family}",
                "productstructure": 1,  # 1=Product per Microsoft Learn (2=Family, 3=Bundle)
                "quantitydecimal": 2,    # decimal places allowed for line quantities
                "price": round(rng.uniform(p_min, p_max), 2),
            })
            pid += 1
    for sku_family, name_template, p_min, p_max in SOFTWARE_TEMPLATES:
        for _ in range(7):
            n = int(rng.integers(50, 5000))
            sku = f"{sku_family}-{int(rng.integers(10000, 99999))}"
            out.append({
                "_synthetic_id": short_uuid_from("product", pid),
                "productnumber": sku,
                "name": name_template.format(n=n)[:100],
                "description": f"Software / Service {sku_family}",
                "productstructure": 1,
                "quantitydecimal": 2,
                "price": round(rng.uniform(p_min, p_max), 2),
            })
            pid += 1
    return out


# ---------------------------------------------------------------------------
# Unit-of-measure defaults (required by Product per Microsoft Learn)
# ---------------------------------------------------------------------------

def fetch_unit_defaults(client: "DataverseClient") -> Tuple[str, str]:
    """Return (uomscheduleid, uomid) for the environment's default Unit Group / Unit.

    Every Dataverse environment ships with at least one uomschedule (Unit Group)
    and one uom (Unit) - typically "Default Unit" / "Primary Unit". A product
    requires both, otherwise the create fails with 0x80043b0a
    "The unit schedule id is missing".
    """
    sched_body = client.get("uomschedules", params={
        "$select": "uomscheduleid,name", "$top": "1",
    })
    sched_items = (sched_body or {}).get("value", [])
    if not sched_items:
        sys.exit("No uomschedule found in the environment - cannot create products.")
    schedule_id = sched_items[0]["uomscheduleid"]
    schedule_name = sched_items[0].get("name", "?")

    # Pick a uom that belongs to that schedule.
    uom_body = client.get("uoms", params={
        "$select": "uomid,name,_uomscheduleid_value",
        "$filter": f"_uomscheduleid_value eq {schedule_id}",
        "$top": "1",
    })
    uoms = (uom_body or {}).get("value", [])
    if not uoms:
        # Fallback: any uom
        uom_body = client.get("uoms", params={"$select": "uomid,name,_uomscheduleid_value", "$top": "1"})
        uoms = (uom_body or {}).get("value", [])
        if not uoms:
            sys.exit("No uom found in the environment - cannot create products.")
        schedule_id = uoms[0].get("_uomscheduleid_value", schedule_id)

    uom_id = uoms[0]["uomid"]
    uom_name = uoms[0].get("name", "?")
    print(f"[product] using uomschedule '{schedule_name}' ({schedule_id})", file=sys.stderr)
    print(f"[product] using uom        '{uom_name}' ({uom_id})", file=sys.stderr)
    return schedule_id, uom_id


def patch_products_with_uom_defaults(records: List[dict], schedule_id: str, uom_id: str) -> None:
    """Add the required defaultuom bindings to every product record (in place)."""
    sched_bind = f"/uomschedules({schedule_id})"
    uom_bind = f"/uoms({uom_id})"
    for r in records:
        r["defaultuomscheduleid@odata.bind"] = sched_bind
        r["defaultuomid@odata.bind"] = uom_bind


def build_pricelevels() -> List[dict]:
    return [
        {"_synthetic_id": short_uuid_from("pricelevel", 0), "name": "Standard 2026"},
        {"_synthetic_id": short_uuid_from("pricelevel", 1), "name": "Premium 2026"},
        {"_synthetic_id": short_uuid_from("pricelevel", 2), "name": "Loyalty 2026"},
    ]


# --- Sales timeline: orders, order lines, invoices, invoice lines, payments ---

@dataclass
class InvoiceTimeline:
    """All facts about one invoice that downstream tables need."""
    customer: CustomerSpec
    invoice_synthetic: str
    invoice_number: str
    invoice_date: date
    due_date: date
    skonto_deadline: Optional[date]
    skonto_pct: float
    skonto_amount: float
    netto_amount: float
    tax_rate: float
    tax_amount: float
    gross_amount: float
    is_reverse_charge: bool
    is_disputed: bool
    paid_date: Optional[date]
    days_to_pay: int
    is_partially_paid: bool
    paid_amount: float
    outstanding_amount: float
    plan_synthetic: Optional[str] = None


def days_to_pay(profile: Profile, invoice_date: date, rng: np.random.Generator,
                  customer_index: int, drift_factor: float) -> int:
    """Sample days-to-pay honoring profile rules + drift / Q1 crunch / insolve."""
    mu, sigma = profile.days_to_pay_mu, profile.days_to_pay_sigma
    if profile.cash_crunch_q1 and invoice_date.month in (1, 2, 3):
        mu = 50.0; sigma = 8.0
    if profile.drift:
        # Drift toward 47d in the most recent 6 months
        mu = mu + drift_factor * 19.0
    d = int(round(rng.normal(mu, sigma)))
    return max(1, d)


def generate_invoice_timeline(customers: List[CustomerSpec], rng: np.random.Generator,
                                today: date, months: int) -> List[InvoiceTimeline]:
    timelines: List[InvoiceTimeline] = []
    start_date = today - timedelta(days=30 * months)
    invoice_counter = 0
    for c in customers:
        # New-Deadbeat: account is < 4 months old
        cust_start = (today - timedelta(days=int(rng.integers(30, 120)))) if c.profile.new_deadbeat else start_date
        # Active days in window
        days_active = (today - cust_start).days
        if days_active < 30:
            continue
        # Strategic/loyal customers transact more
        rate_per_year = 5.0
        if c.profile.is_strategic:
            rate_per_year = 12.0
        elif c.profile.name == "Loyal-Skonto-Taker":
            rate_per_year = 6.0
        elif c.profile.new_deadbeat:
            rate_per_year = 3.0  # only had time for 1-2

        n_invoices = max(1, int(rng.poisson(rate_per_year * (days_active / 365.0))))
        # New-Deadbeat: only 1-2 invoices
        if c.profile.new_deadbeat:
            n_invoices = max(1, min(2, n_invoices))

        for k in range(n_invoices):
            invoice_counter += 1
            invoice_synthetic = short_uuid_from("invoice", c.index, k)
            # Uniformly distribute over active window
            offset = int(rng.integers(0, max(1, days_active - 5)))
            invoice_date = cust_start + timedelta(days=offset)
            # Story-hero invoice number assignment (first invoice for hero)
            if c.index < len(STORY_HEROES) and k == 0:
                invoice_number = STORY_HEROES[c.index].invoice_number_seed
            else:
                invoice_number = f"INV-{invoice_date.year}-{invoice_counter:05d}"
            # Drift: more drift for later invoices
            drift_factor = 0.0
            if c.profile.drift and k >= n_invoices - 3:
                drift_factor = (k - (n_invoices - 3) + 1) / 3.0
            # About-To-Insolve: pay normally until last 60 days, then never
            d_pay = days_to_pay(c.profile, invoice_date, rng, c.index, drift_factor)
            # Payment terms: 30 net default
            due_date = invoice_date + timedelta(days=30)
            # Skonto: 2% in 10 days for loyal
            skonto_pct = 2.0 if c.profile.name == "Loyal-Skonto-Taker" else (
                2.0 if rng.random() < 0.4 else 0.0)
            skonto_deadline = invoice_date + timedelta(days=10) if skonto_pct > 0 else None

            # Amounts
            netto = float(round(rng.uniform(800, 28_000), 2))
            # Reverse charge: hero #10 (Bauer) and 100% of EU customers
            is_rc = c.is_eu_non_de
            tax_rate = 0.0 if is_rc else (7.0 if rng.random() < 0.05 else 19.0)
            tax_amount = round(netto * tax_rate / 100.0, 2)
            gross = round(netto + tax_amount, 2)
            skonto_amount = round(gross * skonto_pct / 100.0, 2) if skonto_pct > 0 else 0.0

            # Disputed?
            is_disputed = rng.random() < c.profile.dispute_rate
            if c.index == 4 and k == 0:  # Krueger Logistik hero
                is_disputed = True

            # Hero overrides
            if c.index == 11 and k == 0:  # Hartmann partial payment
                gross = 12_000.00
                netto = round(gross / 1.19, 2)
                tax_amount = round(gross - netto, 2)
                tax_rate = 19.0
                skonto_pct = 0.0
                skonto_amount = 0.0

            # Payment outcome
            paid_date: Optional[date] = None
            paid_amount = 0.0
            outstanding = gross
            partially_paid = False
            today_minus = (today - invoice_date).days
            if c.profile.insolve:
                # Pays normally until the last 60 days, then stops
                if today_minus >= 60 and (today - invoice_date).days <= 60 + d_pay:
                    paid_date = invoice_date + timedelta(days=d_pay)
                    paid_amount = gross
                    outstanding = 0.0
                else:
                    # outstanding/unpaid
                    pass
            elif c.profile.new_deadbeat and k == 0:
                # Don't pay first invoice
                pass
            elif c.index == 5 and k == 0:
                # Lehmann hero: force 75-day-old unpaid invoice so Mahnstufe 3 fires
                invoice_date = today - timedelta(days=75)
                due_date = invoice_date + timedelta(days=30)
                # leave paid_date=None
            elif c.index == 11 and k == 0:
                # Hartmann partial payment story
                paid_date = invoice_date + timedelta(days=20)
                paid_amount = 5_400.00
                outstanding = 6_600.00
                partially_paid = True
            elif today_minus >= d_pay:
                paid_date = invoice_date + timedelta(days=d_pay)
                paid_amount = gross
                outstanding = 0.0

            timelines.append(InvoiceTimeline(
                customer=c, invoice_synthetic=invoice_synthetic, invoice_number=invoice_number,
                invoice_date=invoice_date, due_date=due_date,
                skonto_deadline=skonto_deadline, skonto_pct=skonto_pct,
                skonto_amount=skonto_amount, netto_amount=netto, tax_rate=tax_rate,
                tax_amount=tax_amount, gross_amount=gross, is_reverse_charge=is_rc,
                is_disputed=is_disputed, paid_date=paid_date, days_to_pay=d_pay,
                is_partially_paid=partially_paid, paid_amount=paid_amount,
                outstanding_amount=outstanding,
            ))
    return timelines


def build_salesorders_from_invoices(timelines: List[InvoiceTimeline]) -> Tuple[List[dict], List[dict]]:
    """Each invoice gets an associated salesorder (created 5-15 days earlier)."""
    orders: List[dict] = []
    order_lines: List[dict] = []
    for idx, tl in enumerate(timelines):
        order_synthetic = short_uuid_from("salesorder", tl.customer.index, tl.invoice_synthetic.split(":")[-1])
        order_date = tl.invoice_date - timedelta(days=int((tl.invoice_date - tl.invoice_date).days or 7))
        orders.append({
            "_synthetic_id": order_synthetic,
            "name": f"Order {tl.invoice_number}"[:160],
            "ordernumber": f"ORD-{tl.invoice_date.year}-{idx+1:05d}",
            "submitdate": (tl.invoice_date - timedelta(days=7)).isoformat() + "T00:00:00Z",
            "totalamount": tl.gross_amount,
            "freightamount": 0.0,
            "customerid_account@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
        })
        # 1-3 lines
        n_lines = (idx % 3) + 1
        for ln in range(n_lines):
            order_lines.append({
                "_synthetic_id": short_uuid_from("salesorderdetail", tl.customer.index, tl.invoice_synthetic.split(":")[-1], ln),
                "salesorderid@odata.bind": f"_synthetic:{order_synthetic}",
                "isproductoverridden": True,
                "productdescription": f"Lieferposition {ln+1}",
                "quantity": float((ln + 1) * 5),
                "priceperunit": round(tl.netto_amount / max(1, n_lines * (ln + 1) * 5), 2),
                "extendedamount": round(tl.netto_amount / max(1, n_lines), 2),
            })
    return orders, order_lines


def build_invoices_and_lines(timelines: List[InvoiceTimeline]) -> Tuple[List[dict], List[dict]]:
    invoices: List[dict] = []
    invoice_lines: List[dict] = []
    for tl in timelines:
        invoices.append({
            "_synthetic_id": tl.invoice_synthetic,
            "name": tl.invoice_number[:160],
            "invoicenumber": tl.invoice_number,
            "duedate": tl.due_date.isoformat() + "T00:00:00Z",
            "datedelivered": (tl.invoice_date - timedelta(days=2)).isoformat() + "T00:00:00Z",
            "totalamount": tl.gross_amount,
            "freightamount": 0.0,
            "totaltax": tl.tax_amount,
            "customerid_account@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
            f"{PREFIX}_taxrate": tl.tax_rate,
            f"{PREFIX}_taxamount": tl.tax_amount,
            f"{PREFIX}_netamount": tl.netto_amount,
            f"{PREFIX}_grossamount": tl.gross_amount,
            f"{PREFIX}_reversecharge": tl.is_reverse_charge,
            f"{PREFIX}_buyervatid": tl.customer.ustidnr,
            f"{PREFIX}_skontopercent": tl.skonto_pct,
            f"{PREFIX}_skontodeadline": (tl.skonto_deadline.isoformat() + "T00:00:00Z") if tl.skonto_deadline else None,
            f"{PREFIX}_skontoamount": tl.skonto_amount,
            f"{PREFIX}_skontoclaimed": tl.paid_date is not None and tl.skonto_deadline is not None
                and tl.paid_date <= tl.skonto_deadline,
            f"{PREFIX}_nettodeadline": tl.due_date.isoformat() + "T00:00:00Z",
            f"{PREFIX}_paidamount": tl.paid_amount,
            f"{PREFIX}_outstandingamount": tl.outstanding_amount,
            f"{PREFIX}_partiallypaid": tl.is_partially_paid,
            f"{PREFIX}_isdisputed": tl.is_disputed,
            f"{PREFIX}_riskscore": 0.0,  # filled later by risk snapshots
        })
        for ln in range(2):
            invoice_lines.append({
                "_synthetic_id": short_uuid_from("invoicedetail", tl.invoice_synthetic.split(":", 1)[1], ln),
                "invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
                "isproductoverridden": True,
                "productdescription": f"Pos. {ln+1} zu {tl.invoice_number}",
                "quantity": float(ln + 1),
                "priceperunit": round(tl.netto_amount / 2, 2),
                "extendedamount": round(tl.netto_amount / 2, 2),
                f"{PREFIX}_linetaxrate": tl.tax_rate,
                f"{PREFIX}_linetaxamount": round(tl.tax_amount / 2, 2),
                f"{PREFIX}_linenetamount": round(tl.netto_amount / 2, 2),
            })
    return invoices, invoice_lines


def build_delivery_notes(timelines: List[InvoiceTimeline], orders_by_invoice: Dict[str, str],
                          rng: np.random.Generator) -> List[dict]:
    out = []
    for tl in timelines:
        if rng.random() > 0.7:
            continue
        order_synthetic = orders_by_invoice.get(tl.invoice_synthetic)
        if not order_synthetic:
            continue
        out.append({
            "_synthetic_id": short_uuid_from("deliverynote", tl.invoice_synthetic.split(":", 1)[1]),
            f"{PREFIX}_name": f"LS-{tl.invoice_date.year}-{tl.customer.index:05d}-{tl.invoice_synthetic.split(':')[-1]}",
            f"{PREFIX}_deliverydate": (tl.invoice_date - timedelta(days=2)).isoformat() + "T00:00:00Z",
            f"{PREFIX}_carrier": rng.choice(["DHL Freight", "Schenker", "Dachser", "Hermes Einrichtungs Service"]),
            f"{PREFIX}_trackingnumber": f"TRK{int(rng.integers(100000000, 999999999))}",
            f"{PREFIX}_signedbyname": GERMAN_FIRST_NAMES[int(rng.integers(0, len(GERMAN_FIRST_NAMES)))] + " "
                                       + GERMAN_SURNAMES[int(rng.integers(0, len(GERMAN_SURNAMES)))],
            f"{PREFIX}_signedimageurl": f"https://muellersp.sharepoint.com/pod/{tl.customer.index}-{tl.invoice_synthetic.split(':')[-1]}.png",
            f"{PREFIX}_orderid@odata.bind": f"_synthetic:{order_synthetic}",
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
        })
    return out


def build_payment_events(timelines: List[InvoiceTimeline]) -> List[dict]:
    out = []
    for tl in timelines:
        if tl.paid_date is None:
            continue
        out.append({
            "_synthetic_id": short_uuid_from("paymentevent", tl.invoice_synthetic.split(":", 1)[1]),
            f"{PREFIX}_name": f"PAY-{tl.invoice_number}",
            f"{PREFIX}_paymentdate": tl.paid_date.isoformat() + "T00:00:00Z",
            f"{PREFIX}_amount": tl.paid_amount,
            f"{PREFIX}_paymentmethod": OPT_BASE + (0 if tl.customer.iban else 1),  # SEPA / wire
            f"{PREFIX}_bankreference": f"REF-{tl.invoice_synthetic.split(':')[-1]}",
            f"{PREFIX}_dayslate": max(0, (tl.paid_date - tl.due_date).days),
            f"{PREFIX}_exchangerate": 1.0,
            f"{PREFIX}_baseamount": tl.paid_amount,
            f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
        })
    return out


def build_payment_plans(timelines: List[InvoiceTimeline], rng: np.random.Generator) -> Tuple[List[dict], List[dict]]:
    plans: List[dict] = []
    installments: List[dict] = []
    for tl in timelines:
        # Plans for ~5% of overdue invoices, plus mandatory hero #14 (Schaefer)
        eligible = (tl.outstanding_amount > 0 and (tl.customer.profile.name in
                     ("Repeat-Late-Payer", "Slow-Drifter") and rng.random() < 0.1))
        if tl.customer.index == 14 and tl.invoice_synthetic.endswith(":0"):
            eligible = True
        if not eligible:
            continue
        plan_synthetic = short_uuid_from("paymentplan", tl.invoice_synthetic.split(":", 1)[1])
        n_install = 4 if tl.customer.index == 14 else int(rng.integers(2, 5))
        plans.append({
            "_synthetic_id": plan_synthetic,
            f"{PREFIX}_name": f"PP-{tl.invoice_date.year}-{tl.customer.index:04d}",
            f"{PREFIX}_totalamount": tl.outstanding_amount,
            f"{PREFIX}_installmentcount": n_install,
            f"{PREFIX}_startdate": (tl.due_date + timedelta(days=14)).isoformat() + "T00:00:00Z",
            f"{PREFIX}_status": OPT_BASE + 1,  # Active
            f"{PREFIX}_accepteddate": (tl.due_date + timedelta(days=10)).isoformat() + "T00:00:00Z",
            f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
        })
        per = round(tl.outstanding_amount / n_install, 2)
        for i in range(n_install):
            installments.append({
                "_synthetic_id": short_uuid_from("planinstallment", plan_synthetic, i),
                f"{PREFIX}_name": f"Rate {i+1}/{n_install}",
                f"{PREFIX}_sequence": i + 1,
                f"{PREFIX}_duedate": (tl.due_date + timedelta(days=14 + 30*i)).isoformat() + "T00:00:00Z",
                f"{PREFIX}_amount": per,
                f"{PREFIX}_status": OPT_BASE + 0,  # Pending
                f"{PREFIX}_paymentplanid@odata.bind": f"_synthetic:{plan_synthetic}",
            })
        tl.plan_synthetic = plan_synthetic
    return plans, installments


def build_credit_signals(customers: List[CustomerSpec], rng: np.random.Generator,
                          today: date) -> List[dict]:
    out = []
    for c in customers:
        # Insolve customers always get a critical Insolvency Notice 45-60 days ago
        if c.profile.insolve:
            sig_date = today - timedelta(days=int(rng.integers(45, 61)))
            # Hero #13 (Neumann) gets a fixed date
            if c.index == 13:
                sig_date = date(2026, 4, 15)
            out.append({
                "_synthetic_id": short_uuid_from("creditsignal", c.index, 0),
                f"{PREFIX}_name": f"INSOLV-{c.index:05d}",
                f"{PREFIX}_source": OPT_BASE + 0,         # Creditreform
                f"{PREFIX}_signaltype": OPT_BASE + 1,    # Insolvency
                f"{PREFIX}_severity": OPT_BASE + 2,      # Critical
                f"{PREFIX}_score": c.creditreform_score,
                f"{PREFIX}_receiveddate": sig_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_payload": json.dumps({"source": "Creditreform", "type": "Insolvency Notice",
                                                  "ref": f"CR-{c.index:08d}"}),
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
            })
        # Most customers also have routine score-change signals
        n = int(rng.integers(1, 3))
        for k in range(n):
            sig_date = today - timedelta(days=int(rng.integers(30, 360)))
            out.append({
                "_synthetic_id": short_uuid_from("creditsignal", c.index, k + 1),
                f"{PREFIX}_name": f"CR-{c.index:05d}-{k+1}",
                f"{PREFIX}_source": OPT_BASE + 0,
                f"{PREFIX}_signaltype": OPT_BASE + 0,    # ScoreChange
                f"{PREFIX}_severity": OPT_BASE + 0,      # Info
                f"{PREFIX}_score": c.creditreform_score + float(rng.uniform(-20, 20)),
                f"{PREFIX}_receiveddate": sig_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_payload": json.dumps({"source": "Creditreform", "type": "ScoreChange"}),
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
            })
    return out


def build_risk_snapshots(timelines: List[InvoiceTimeline], today: date,
                          rng: np.random.Generator) -> List[dict]:
    """Weekly risk snapshots over the last 13 weeks for invoices that were open."""
    out = []
    for tl in timelines:
        for w in range(13):
            snap_date = today - timedelta(days=7 * w)
            if snap_date < tl.invoice_date:
                continue
            if tl.paid_date is not None and snap_date > tl.paid_date:
                continue
            # Compute score using documented formula
            elapsed = (snap_date - tl.invoice_date).days / max(1, (tl.due_date - tl.invoice_date).days)
            late_baseline = max(0.0, (tl.customer.profile.days_to_pay_mu - 30) / 30)
            cr_penalty = max(0.0, (300 - tl.customer.creditreform_score) / 300)
            seasonal = 0.2 if (tl.customer.profile.cash_crunch_q1 and snap_date.month in (1,2,3)) else 0.0
            score = (0.4 * elapsed + 0.25 * late_baseline + 0.15 * cr_penalty
                     + 0.1 * 0.5 + 0.1 * seasonal)
            score = max(0.0, min(1.0, round(score + float(rng.normal(0, 0.03)), 3)))
            out.append({
                "_synthetic_id": short_uuid_from("risksnap", tl.invoice_synthetic.split(":", 1)[1], w),
                f"{PREFIX}_name": f"RS-{tl.invoice_number}-{snap_date.isoformat()}",
                f"{PREFIX}_snapshotdate": snap_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_riskscore": score,
                f"{PREFIX}_modelversion": "agent-classifier-v1",
                f"{PREFIX}_topfeatures": json.dumps({"elapsed_due_ratio": round(elapsed, 2),
                                                      "late_baseline": round(late_baseline, 2),
                                                      "cr_penalty": round(cr_penalty, 2)}),
                f"{PREFIX}_thresholdcrossed": score >= 0.7,
                f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
            })
    return out


def build_dunning_events(timelines: List[InvoiceTimeline], today: date,
                          rng: np.random.Generator) -> List[dict]:
    out = []
    stages = [(5, 0, 0), (15, 1, 1), (30, 2, 2), (45, 3, 2), (90, 4, 2)]
    # (days_after_due, stage_value_offset, tone_value_offset_for_strict-ish)
    for tl in timelines:
        if tl.paid_date is not None:
            continue  # paid invoices don't get dunned
        if tl.customer.profile.suppress_automation:
            # Strategic: only WhiteGlove personal note at +5
            sent_date = tl.due_date + timedelta(days=5)
            if sent_date > today:
                continue
            out.append({
                "_synthetic_id": short_uuid_from("dunning", tl.invoice_synthetic.split(":", 1)[1], 0),
                f"{PREFIX}_name": f"DUN-{tl.invoice_number}-WhiteGlove",
                f"{PREFIX}_stage": OPT_BASE + 0,
                f"{PREFIX}_tone": OPT_BASE + 3,  # WhiteGlove
                f"{PREFIX}_channel": OPT_BASE + 0,  # Email
                f"{PREFIX}_sentdate": sent_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_responsereceived": rng.random() < 0.6,
                f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
            })
            continue
        # Standard escalation
        days_overdue = (today - tl.due_date).days
        for offset, stage_off, tone_off in stages:
            if days_overdue >= offset:
                sent = tl.due_date + timedelta(days=offset)
                if sent > today:
                    break
                # Repeat-late and insolve push to FinalDemand/Legal
                if stage_off >= 3 and not (tl.customer.profile.name in
                                             ("Repeat-Late-Payer",) or tl.customer.profile.insolve
                                             or tl.customer.profile.new_deadbeat):
                    break
                tone = (OPT_BASE + 0) if stage_off == 0 else (OPT_BASE + 1) if stage_off == 1 else (OPT_BASE + 2)
                channel = (OPT_BASE + 0) if stage_off <= 1 else (OPT_BASE + 1)  # Email then Letter
                out.append({
                    "_synthetic_id": short_uuid_from("dunning", tl.invoice_synthetic.split(":", 1)[1], offset),
                    f"{PREFIX}_name": f"DUN-{tl.invoice_number}-Stufe{stage_off+1}",
                    f"{PREFIX}_stage": OPT_BASE + stage_off,
                    f"{PREFIX}_tone": tone,
                    f"{PREFIX}_channel": channel,
                    f"{PREFIX}_sentdate": sent.isoformat() + "T00:00:00Z",
                    f"{PREFIX}_responsereceived": rng.random() < 0.3,
                    f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
                    f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
                })
            else:
                break
    return out


def build_emails(customers: List[CustomerSpec], timelines: List[InvoiceTimeline],
                  rng: np.random.Generator, today: date) -> List[dict]:
    out = []
    for c in customers:
        # 8 emails per active customer
        cust_invoices = [t for t in timelines if t.customer.index == c.index]
        if not cust_invoices:
            continue
        n = int(rng.normal(8, 2))
        for k in range(max(2, n)):
            tl = cust_invoices[int(rng.integers(0, len(cust_invoices)))]
            classification_idx = int(rng.integers(0, 5))
            classifications = ["Reschedule", "Dispute", "ProofOfDelivery",
                                 "PaymentConfirmation", "GeneralQuestion"]
            cls = classifications[classification_idx]
            templates = EMAIL_TEMPLATES[cls]
            subj_t, body_t = templates[int(rng.integers(0, len(templates)))]
            anrede = "Sehr geehrte Damen und Herren" if c.profile.formality == "Sie" else "Hallo"
            ctx = {
                "anrede": anrede,
                "invoice_number": tl.invoice_number,
                "amount_eur": f"{tl.gross_amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "due_date": tl.due_date.strftime("%d.%m.%Y"),
                "customer_name": c.name,
            }
            sent = tl.invoice_date + timedelta(days=int(rng.integers(0, 60)))
            if sent > today:
                sent = today
            out.append({
                "_synthetic_id": short_uuid_from("email", c.index, k),
                "subject": subj_t.format(**ctx)[:200],
                "description": body_t.format(**ctx),
                "actualstart": sent.isoformat() + "T09:00:00Z",
                "actualend": sent.isoformat() + "T09:00:00Z",
                "directioncode": False,  # incoming
                "regardingobjectid_invoice_email@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
                f"{PREFIX}_classification": OPT_BASE + classification_idx,
                f"{PREFIX}_languagedetected": "de-DE",
                f"{PREFIX}_sentimentscore": float(round(rng.uniform(-0.6, 0.5), 2)),
                f"{PREFIX}_agentdrafted": False,
            })
    return out


def build_simple_activities(customers: List[CustomerSpec], timelines: List[InvoiceTimeline],
                              rng: np.random.Generator, today: date,
                              kind: str, per_customer_avg: int) -> List[dict]:
    """Generate task / phonecall / appointment / annotation records (lightweight)."""
    out = []
    for c in customers:
        cust_invoices = [t for t in timelines if t.customer.index == c.index]
        n = max(1, int(rng.normal(per_customer_avg, max(1, per_customer_avg / 2))))
        for k in range(n):
            tl = cust_invoices[int(rng.integers(0, len(cust_invoices)))] if cust_invoices else None
            sent = today - timedelta(days=int(rng.integers(0, 700)))
            base = {
                "_synthetic_id": short_uuid_from(kind, c.index, k),
                "subject": f"{kind.capitalize()} re. {tl.invoice_number if tl else c.name}"[:200],
                "description": f"Automatisch generierte {kind} Notiz fuer Demo-Daten.",
                "actualstart": sent.isoformat() + "T10:00:00Z",
                "actualend": sent.isoformat() + "T10:30:00Z",
            }
            if tl:
                base[f"regardingobjectid_invoice_{kind}@odata.bind"] = f"_synthetic:{tl.invoice_synthetic}"
            else:
                base[f"regardingobjectid_account_{kind}@odata.bind"] = f"_synthetic:{c.synthetic_id}"
            out.append(base)
    return out


def build_letters(timelines: List[InvoiceTimeline], rng: np.random.Generator) -> List[dict]:
    """Generate dunning letters for the strict tones."""
    out = []
    for tl in timelines:
        if tl.paid_date or not (tl.customer.profile.name in
                                  ("Repeat-Late-Payer", "Slow-Drifter") or tl.customer.profile.insolve):
            continue
        tone = "Strict" if not tl.customer.profile.suppress_automation else "WhiteGlove"
        subj_t, body_t = DUNNING_TEMPLATES[tone]
        anrede = "Sehr geehrte Damen und Herren" if tl.customer.profile.formality == "Sie" else "Hallo"
        # Hero #12 (Klein) gets personalized salutation
        if tl.customer.index == 12:
            anrede = "Sehr geehrter Herr Klein"
        new_deadline = (tl.due_date + timedelta(days=14)).strftime("%d.%m.%Y")
        out.append({
            "_synthetic_id": short_uuid_from("letter", tl.invoice_synthetic.split(":", 1)[1]),
            "subject": subj_t.format(invoice_number=tl.invoice_number)[:200],
            "description": body_t.format(
                anrede=anrede,
                invoice_number=tl.invoice_number,
                amount_eur=f"{tl.outstanding_amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                invoice_date=tl.invoice_date.strftime("%d.%m.%Y"),
                due_date=tl.due_date.strftime("%d.%m.%Y"),
                new_deadline=new_deadline,
            ),
            "directioncode": True,  # outgoing
            "regardingobjectid_invoice_letter@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
        })
    return out


def build_incidents(timelines: List[InvoiceTimeline], rng: np.random.Generator) -> List[dict]:
    out = []
    for tl in timelines:
        if not tl.is_disputed:
            continue
        # Hero #4 (Krueger) gets PriceMismatch type explicitly
        dispute_type_idx = 0 if tl.customer.index == 4 else int(rng.integers(0, 6))
        out.append({
            "_synthetic_id": short_uuid_from("incident", tl.invoice_synthetic.split(":", 1)[1]),
            "title": f"Reklamation {tl.invoice_number}"[:200],
            "description": "Kundenreklamation - Pruefung erforderlich.",
            "ticketnumber": f"TKT-{tl.invoice_date.year}-{tl.customer.index:05d}",
            "customerid_account@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
            f"{PREFIX}_disputetype": OPT_BASE + dispute_type_idx,
            f"{PREFIX}_resolutiondays": int(rng.integers(3, 30)),
            f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
        })
    return out


def build_agent_decisions(customers: List[CustomerSpec], timelines: List[InvoiceTimeline],
                            rng: np.random.Generator) -> List[dict]:
    out = []
    decision_id = 0
    for tl in timelines:
        # 1 RiskScored decision per invoice (open or recently scored)
        out.append({
            "_synthetic_id": short_uuid_from("agentdecision", "risk", decision_id),
            f"{PREFIX}_name": f"DEC-RISK-{tl.invoice_number}",
            f"{PREFIX}_decisiontype": OPT_BASE + 0,  # RiskScored
            f"{PREFIX}_scenario": OPT_BASE + 0,
            f"{PREFIX}_decidedat": tl.invoice_date.isoformat() + "T00:00:00Z",
            f"{PREFIX}_reasoning": f"Risikoscoring fuer Rechnung {tl.invoice_number} basierend auf Profil "
                                    f"{tl.customer.profile.name} und Zahlungshistorie.",
            f"{PREFIX}_inputshash": hashlib.sha1(tl.invoice_number.encode()).hexdigest()[:16],
            f"{PREFIX}_inputspayload": json.dumps({"profile": tl.customer.profile.name,
                                                     "creditreform": tl.customer.creditreform_score,
                                                     "amount": tl.gross_amount}),
            f"{PREFIX}_outcome": OPT_BASE + 0,  # Executed
            f"{PREFIX}_confidencescore": float(round(rng.uniform(0.65, 0.99), 2)),
            f"{PREFIX}_modelname": "agent-classifier-v1",
            f"{PREFIX}_promptversion": "v1.0",
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
            f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
        })
        decision_id += 1
        # Strategic accounts: every dunning is Suppressed/RoutedToHuman.
        # NOTE: mueller_decisiontype and mueller_scenario were provisioned with
        # only the placeholder "Default" option (no parseable options in
        # purpose), so we can only legally write OPT_BASE+0 here. mueller_outcome
        # was provisioned with 4 options so OPT_BASE+2 (RoutedToHuman) is fine.
        if tl.customer.profile.suppress_automation and tl.outstanding_amount > 0:
            out.append({
                "_synthetic_id": short_uuid_from("agentdecision", "supp", decision_id),
                f"{PREFIX}_name": f"DEC-SUPPRESS-{tl.invoice_number}",
                f"{PREFIX}_decisiontype": OPT_BASE + 0,
                f"{PREFIX}_scenario": OPT_BASE + 0,
                f"{PREFIX}_decidedat": tl.due_date.isoformat() + "T00:00:00Z",
                f"{PREFIX}_reasoning": f"Account ist als strategisch markiert (mueller_isstrategicaccount=true). "
                                        f"Auto-Dunning unterdrueckt; Eskalation an KAM.",
                f"{PREFIX}_outcome": OPT_BASE + 2,  # RoutedToHuman
                f"{PREFIX}_confidencescore": 0.95,
                f"{PREFIX}_modelname": "agent-classifier-v1",
                f"{PREFIX}_promptversion": "v1.0",
                f"{PREFIX}_customerid@odata.bind": f"_synthetic:{tl.customer.synthetic_id}",
                f"{PREFIX}_invoiceid@odata.bind": f"_synthetic:{tl.invoice_synthetic}",
            })
            decision_id += 1
    return out


def build_churn_signals(customers: List[CustomerSpec], rng: np.random.Generator,
                          today: date) -> List[dict]:
    out = []
    for c in customers:
        if rng.random() > 0.05:
            continue
        out.append({
            "_synthetic_id": short_uuid_from("churnsignal", c.index),
            f"{PREFIX}_name": f"CHURN-{c.accountnumber}",
            f"{PREFIX}_signaltype": OPT_BASE + 0,
            f"{PREFIX}_severity": OPT_BASE + 1,  # Medium
            f"{PREFIX}_detecteddate": (today - timedelta(days=int(rng.integers(7, 60)))).isoformat() + "T00:00:00Z",
            f"{PREFIX}_baselinevalue": float(round(rng.uniform(2, 8), 1)),
            f"{PREFIX}_currentvalue": float(round(rng.uniform(0.5, 2), 1)),
            f"{PREFIX}_customerid@odata.bind": f"_synthetic:{c.synthetic_id}",
        })
    return out


def build_contracts(customers: List[CustomerSpec], rng: np.random.Generator,
                     today: date) -> Tuple[List[dict], List[dict]]:
    contracts: List[dict] = []
    contract_lines: List[dict] = []
    for c in customers:
        if rng.random() > 0.3:
            continue
        start = today - timedelta(days=int(rng.integers(60, 700)))
        end = start + timedelta(days=365)
        cs = short_uuid_from("contract", c.index)
        # Hero #9 (Fischer) gets a contract ending 90 days from today
        if c.index == 9:
            end = today + timedelta(days=90)
            start = end - timedelta(days=365)
        contracts.append({
            "_synthetic_id": cs,
            "title": f"Wartungsvertrag {c.accountnumber}"[:160],
            "contractnumber": f"CON-{2026}-{c.index:05d}",
            "activeon": start.isoformat() + "T00:00:00Z",
            "expireson": end.isoformat() + "T00:00:00Z",
            "totalprice": float(round(rng.uniform(5_000, 80_000), 2)),
            "billingstartdate": start.isoformat() + "T00:00:00Z",
            "billingendon": end.isoformat() + "T00:00:00Z",
            "customerid_account@odata.bind": f"_synthetic:{c.synthetic_id}",
            "billingcustomerid_account@odata.bind": f"_synthetic:{c.synthetic_id}",
            f"{PREFIX}_renewaltriggerdate": (end - timedelta(days=90)).isoformat() + "T00:00:00Z",
            f"{PREFIX}_billingrhythm": OPT_BASE + 0,  # Annual
            f"{PREFIX}_autorenewenabled": True,
        })
        for ln in range(2):
            contract_lines.append({
                "_synthetic_id": short_uuid_from("contractdetail", c.index, ln),
                "title": f"Vertragsposition {ln + 1}"[:160],
                "quantity": 1.0,
                "price": float(round(rng.uniform(2_500, 18_000), 2)),
                "contractid@odata.bind": f"_synthetic:{cs}",
            })
    return contracts, contract_lines


# ---------------------------------------------------------------------------
# Story-pattern verification
# ---------------------------------------------------------------------------

def verify_story_patterns(customers: List[CustomerSpec], timelines: List[InvoiceTimeline],
                            dunning: List[dict], plans: List[dict], signals: List[dict],
                            incidents: List[dict], letters: List[dict]) -> List[Tuple[str, bool]]:
    """Return list of (pattern_label, ok)."""
    results: List[Tuple[str, bool]] = []
    cust_by_idx = {c.index: c for c in customers}
    inv_by_cust: Dict[int, List[InvoiceTimeline]] = {}
    for tl in timelines:
        inv_by_cust.setdefault(tl.customer.index, []).append(tl)

    for hero in STORY_HEROES:
        c = cust_by_idx.get(hero.index)
        ok = c is not None and c.profile.name == hero.profile_name
        # Additional pattern-specific checks
        if ok and hero.index == 1:  # Weber About-To-Insolve - Creditreform Critical signal
            ok = any(s.get(f"{PREFIX}_signaltype") == OPT_BASE + 1
                     and s.get(f"{PREFIX}_customerid@odata.bind", "").endswith(c.synthetic_id)
                     for s in signals)
        if ok and hero.index == 4:  # Krueger Disputer
            ok = any(i.get(f"{PREFIX}_disputetype") == OPT_BASE
                     and i.get("customerid_account@odata.bind", "").endswith(c.synthetic_id)
                     for i in incidents)
        if ok and hero.index == 5:  # Lehmann Mahnstufe 3
            ok = any(d.get(f"{PREFIX}_stage") in (OPT_BASE + 2, OPT_BASE + 3)
                     and d.get(f"{PREFIX}_customerid@odata.bind", "").endswith(c.synthetic_id)
                     for d in dunning)
        if ok and hero.index == 6:  # Becker Loyal-Skonto-Taker
            ok = any(t.skonto_pct > 0 and t.paid_date is not None and
                       t.skonto_deadline is not None and t.paid_date <= t.skonto_deadline
                       for t in inv_by_cust.get(c.index, []))
        if ok and hero.index == 11:  # Hartmann partial payment
            ok = any(t.is_partially_paid for t in inv_by_cust.get(c.index, []))
        if ok and hero.index == 14:  # Schaefer payment plan
            ok = any(p.get(f"{PREFIX}_customerid@odata.bind", "").endswith(c.synthetic_id)
                     for p in plans)
        results.append((f"[{hero.index:02d}] {hero.company_name} ({hero.profile_name}) - {hero.description}", ok))
    return results


# ---------------------------------------------------------------------------
# Insert pipeline
# ---------------------------------------------------------------------------

# Maps logical_name -> entity_set_name (Web API path segment).
ENTITY_SETS = {
    "transactioncurrency": "transactioncurrencies",
    "pricelevel": "pricelevels",
    "product": "products",
    "account": "accounts",
    "contact": "contacts",
    "customeraddress": "customeraddresses",
    "salesorder": "salesorders",
    "salesorderdetail": "salesorderdetails",
    "invoice": "invoices",
    "invoicedetail": "invoicedetails",
    "contract": "contracts",
    "contractdetail": "contractdetails",
    "email": "emails",
    "task": "tasks",
    "phonecall": "phonecalls",
    "appointment": "appointments",
    "letter": "letters",
    "annotation": "annotations",
    "incident": "incidents",
    f"{PREFIX}_communicationpreference": f"{PREFIX}_communicationpreferences",
    f"{PREFIX}_sepamandate": f"{PREFIX}_sepamandates",
    f"{PREFIX}_lifetimevaluesnapshot": f"{PREFIX}_lifetimevaluesnapshots",
    f"{PREFIX}_deliverynote": f"{PREFIX}_deliverynotes",
    f"{PREFIX}_paymentevent": f"{PREFIX}_paymentevents",
    f"{PREFIX}_paymentplan": f"{PREFIX}_paymentplans",
    f"{PREFIX}_paymentplaninstallment": f"{PREFIX}_paymentplaninstallments",
    f"{PREFIX}_creditsignal": f"{PREFIX}_creditsignals",
    f"{PREFIX}_riskscoresnapshot": f"{PREFIX}_riskscoresnapshots",
    f"{PREFIX}_dunningevent": f"{PREFIX}_dunningevents",
    f"{PREFIX}_churnsignal": f"{PREFIX}_churnsignals",
    f"{PREFIX}_agentdecision": f"{PREFIX}_agentdecisions",
}


# Registry of single-target lookup field name -> target entity logical name.
# Single-target custom lookups don't carry a "_<target>" suffix, so we can't
# infer the target from the field name; we look it up here.
LOOKUP_TARGET_REGISTRY: Dict[str, str] = {
    f"{PREFIX}_customerid": "account",
    f"{PREFIX}_invoiceid": "invoice",
    f"{PREFIX}_orderid": "salesorder",
    f"{PREFIX}_paymentplanid": f"{PREFIX}_paymentplan",
    f"{PREFIX}_paymenteventid": f"{PREFIX}_paymentevent",
    f"{PREFIX}_currencyid": "transactioncurrency",
    f"{PREFIX}_emailid": "email",
    f"{PREFIX}_letterid": "letter",
    f"{PREFIX}_acceptedby": "contact",
    f"{PREFIX}_overrideby": "systemuser",
    f"{PREFIX}_routedtouserid": "systemuser",
    f"{PREFIX}_keyaccountmanager": "systemuser",
    f"{PREFIX}_holdoverrideby": "systemuser",
    "salesorderid": "salesorder",
    "invoiceid": "invoice",
    "contractid": "contract",
    "primarycontactid": "contact",
    "transactioncurrencyid": "transactioncurrency",
}


def _infer_lookup_target(field_name: str) -> str:
    """Resolve target entity logical name for an @odata.bind field.

    Rules:
        1. Polymorphic activity: 'regardingobjectid_<target>_<source>' -> target.
        2. Single-target known custom lookup: registry hit by base name.
        3. Polymorphic standard FK with '_<target>' suffix: last underscore-token.
    """
    base = field_name.replace("@odata.bind", "")
    if base.startswith("regardingobjectid_"):
        rest = base[len("regardingobjectid_"):]
        return rest.split("_", 1)[0]
    if base in LOOKUP_TARGET_REGISTRY:
        return LOOKUP_TARGET_REGISTRY[base]
    # Strip mueller_ prefix for the registry probe (handles e.g. mueller_xxxid)
    m = re.search(r"_([a-z][a-z0-9_]*)$", base)
    return m.group(1) if m else base


def resolve_synthetic_bindings(record: dict, state: State) -> dict:
    """Replace any '_synthetic:<id>' placeholders with real GUID bindings.

    Strips internal-only fields (_synthetic_id, anything starting with _).
    """
    out = {}
    for k, v in record.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str) and v.startswith("_synthetic:"):
            synthetic_id = v[len("_synthetic:"):]
            real = state.synthetic_to_real(synthetic_id)
            if real is None:
                raise RuntimeError(f"Unresolved synthetic FK: {synthetic_id} (referenced by field {k})")
            target_entity = _infer_lookup_target(k)
            target_set = ENTITY_SETS.get(target_entity, target_entity + "s")
            out[k] = f"/{target_set}({real})"
        else:
            out[k] = v
    return out


def insert_table(client: DataverseClient, state: State, table_logical: str,
                  records: List[dict], dry_run: bool, batch_size: int) -> int:
    """Insert records via $batch.  Skip records whose synthetic_id is already mapped."""
    entity_set = ENTITY_SETS[table_logical]
    inserted = 0
    skipped = 0
    pending: List[Tuple[dict, str]] = []  # (resolved_body, synthetic_id)
    total = len(records)
    for record in records:
        synthetic_id = record["_synthetic_id"]
        if state.synthetic_to_real(synthetic_id):
            skipped += 1
            continue
        body = resolve_synthetic_bindings(record, state)
        pending.append((body, synthetic_id))
        if len(pending) >= batch_size:
            inserted += _flush_batch(client, state, entity_set, pending, dry_run)
            pending.clear()
            print(f"    ... {inserted}/{total - skipped} (skipped existing: {skipped})", file=sys.stderr)
    if pending:
        inserted += _flush_batch(client, state, entity_set, pending, dry_run)
    print(f"  [{table_logical}] inserted {inserted}, skipped {skipped} (already mapped)", file=sys.stderr)
    return inserted


def hydrate_orphans_by_business_key(
    client: "DataverseClient", state: State, table_logical: str,
    entity_set: str, pk_field: str, business_key_col: str,
    records: List[dict], filter_prefix: str,
) -> int:
    """Recover record GUIDs whose business key already exists in Dataverse.

    Closes the small idempotency gap: if a prior $batch POST succeeded but the
    process died before state.save_id_map(), the records exist in Dataverse
    without a local mapping. Without recovery, a re-run hits the unique-key
    alternate-key constraint (0x80040237) on retry. We page through existing
    rows whose business key starts with our deterministic prefix, match them
    to our generated records by that key, and populate id_map.
    """
    want: Dict[str, str] = {}
    for r in records:
        sid = r["_synthetic_id"]
        if state.synthetic_to_real(sid):
            continue
        v = r.get(business_key_col)
        if v:
            want[v] = sid
    if not want:
        return 0
    print(f"[{table_logical}] orphan check: scanning existing rows with "
          f"{business_key_col} startswith '{filter_prefix}' ...", file=sys.stderr)
    recovered = 0
    page = client.get(entity_set, params={
        "$select": f"{pk_field},{business_key_col}",
        "$filter": f"startswith({business_key_col},'{filter_prefix}')",
    })
    while True:
        for item in (page or {}).get("value", []):
            key = item.get(business_key_col)
            sid = want.pop(key, None)
            if sid:
                state.record(sid, item[pk_field].lower())
                recovered += 1
        next_url = (page or {}).get("@odata.nextLink")
        if not next_url or not want:
            break
        page = client.get(next_url)
    if recovered:
        state.save_id_map()
    print(f"[{table_logical}] recovered {recovered} orphan IDs "
          f"(remaining to insert: {len(want)})", file=sys.stderr)
    return recovered


def _run_single_record_probe(client: "DataverseClient", state: State,
                              table_logical: str, entity_set: str,
                              records: List[dict]) -> None:
    """POST exactly one not-yet-mapped record to validate field shape end-to-end.

    Used before bulk batch insert for tables with brittle requirements
    (product needs UoM bindings; customeraddress needs addressnumber>=3).
    """
    probe = next((r for r in records
                  if not state.synthetic_to_real(r["_synthetic_id"])), None)
    if probe is None:
        return  # everything already inserted
    print(f"[{table_logical}] verification probe: inserting 1 record...",
          file=sys.stderr)
    body = resolve_synthetic_bindings(probe, state)
    resp = client.post(entity_set, json=body)
    loc = resp.headers.get("OData-EntityId", "")
    m = re.search(rf"{entity_set}\(([0-9a-f-]+)\)", loc)
    if not m:
        sys.exit(f"{table_logical} probe returned no GUID. Headers: {dict(resp.headers)}")
    state.record(probe["_synthetic_id"], m.group(1).lower())
    state.save_id_map()
    print(f"[{table_logical}] verification OK (real id={m.group(1)})", file=sys.stderr)


def _flush_batch(client: DataverseClient, state: State, entity_set: str,
                  pending: List[Tuple[dict, str]], dry_run: bool) -> int:
    if dry_run:
        # In dry-run, still record synthetic_id -> deterministic fake GUID so
        # downstream tables can resolve their bindings during preview.
        for body, sid in pending:
            fake = uuid.UUID(hashlib.md5(sid.encode()).hexdigest()).hex
            fake = f"{fake[:8]}-{fake[8:12]}-{fake[12:16]}-{fake[16:20]}-{fake[20:32]}"
            state.record(sid, fake)
        return len(pending)
    ops = [{"entityset": entity_set, "body": body} for body, _ in pending]
    guids = client.batch_create(ops)
    for (body, sid), guid in zip(pending, guids):
        state.record(sid, guid)
    state.save_id_map()
    return len(guids)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

INSERT_ORDER = [
    ("pricelevel", "pricelevels"),
    ("product", "products"),
    ("account", "accounts"),
    ("contact", "contacts"),
    ("customeraddress", "addresses"),
    (f"{PREFIX}_communicationpreference", "communication_prefs"),
    (f"{PREFIX}_sepamandate", "sepa_mandates"),
    (f"{PREFIX}_lifetimevaluesnapshot", "ltv_snapshots"),
    ("salesorder", "salesorders"),
    ("salesorderdetail", "salesorder_lines"),
    (f"{PREFIX}_deliverynote", "delivery_notes"),
    ("invoice", "invoices"),
    ("invoicedetail", "invoice_lines"),
    (f"{PREFIX}_paymentevent", "payment_events"),
    (f"{PREFIX}_paymentplan", "payment_plans"),
    (f"{PREFIX}_paymentplaninstallment", "plan_installments"),
    ("contract", "contracts"),
    ("contractdetail", "contract_lines"),
    (f"{PREFIX}_creditsignal", "credit_signals"),
    (f"{PREFIX}_riskscoresnapshot", "risk_snapshots"),
    (f"{PREFIX}_churnsignal", "churn_signals"),
    (f"{PREFIX}_dunningevent", "dunning_events"),
    ("letter", "letters"),
    ("email", "emails"),
    ("task", "tasks"),
    ("phonecall", "phonecalls"),
    ("appointment", "appointments"),
    ("annotation", "annotations"),
    ("incident", "incidents"),
    (f"{PREFIX}_agentdecision", "agent_decisions"),
]


def write_preview(preview_dir: Path, key: str, records: List[dict]) -> None:
    preview_dir.mkdir(parents=True, exist_ok=True)
    path = preview_dir / f"{key}.json"
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")


def parse_args(paths):
    p = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS)
    p.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    p.add_argument("--dry-run", action="store_true",
                   help="(default if --apply not passed) generate preview JSON, no POST")
    p.add_argument("--apply", action="store_true",
                   help="actually POST to Dataverse")
    p.add_argument("--confirm-large", action="store_true",
                   help="required with --apply when total rows > 100,000")
    p.add_argument("--enrich-with-llm", action="store_true",
                   help="enable Stage 2 LLM enrichment (NOT YET IMPLEMENTED)")
    p.add_argument("--resume", action="store_true",
                   help="skip tables already in progress file")
    p.add_argument("--replace", action="store_true",
                   help="DESIGN ONLY in this revision (delete prior data first)")
    p.add_argument("--shortlist", default=str(paths.shortlist_file))
    p.add_argument("--preview-dir", default=str(paths.preview_dir))
    p.add_argument("--state-file", default=str(paths.progress_file))
    p.add_argument("--id-map-file", default=str(paths.id_map_file))
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return p.parse_args()


def main() -> None:
    cfg, paths, limits = load_config()
    args = parse_args(paths)
    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    if args.replace:
        sys.exit("--replace is documented for future work but NOT implemented in this revision.")
    if args.enrich_with_llm:
        print("! --enrich-with-llm is a stub in this revision; emails will use deterministic templates.",
              file=sys.stderr)

    print(f"=== synthetic_data_generator.py ({mode}, seed={args.seed}, "
          f"{args.customers} customers, {args.months} months) ===", file=sys.stderr)

    # Seed everything
    Faker.seed(args.seed)
    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)
    faker = Faker("de_DE")
    today = date.today()

    # State (id_map + progress)
    state = State(Path(args.id_map_file), Path(args.state_file))

    # ---------- Stage 1: structural generation ----------
    print("[gen] customers ...", file=sys.stderr)
    customers = generate_customers(rng, faker, args.customers, STORY_HEROES)

    print("[gen] sales timeline ...", file=sys.stderr)
    timelines = generate_invoice_timeline(customers, rng, today, args.months)

    print("[gen] orders + lines ...", file=sys.stderr)
    orders, order_lines = build_salesorders_from_invoices(timelines)
    orders_by_invoice = {tl.invoice_synthetic: o["_synthetic_id"]
                          for tl, o in zip(timelines, orders)}

    print("[gen] invoices + lines ...", file=sys.stderr)
    invoices, invoice_lines = build_invoices_and_lines(timelines)

    print("[gen] payments + plans ...", file=sys.stderr)
    payment_events = build_payment_events(timelines)
    payment_plans, plan_installments = build_payment_plans(timelines, rng)

    print("[gen] contracts ...", file=sys.stderr)
    contracts, contract_lines = build_contracts(customers, rng, today)

    print("[gen] credit + risk signals ...", file=sys.stderr)
    credit_signals = build_credit_signals(customers, rng, today)
    risk_snapshots = build_risk_snapshots(timelines, today, rng)
    churn_signals = build_churn_signals(customers, rng, today)

    print("[gen] dunning + letters ...", file=sys.stderr)
    dunning = build_dunning_events(timelines, today, rng)
    letters = build_letters(timelines, rng)

    print("[gen] activities ...", file=sys.stderr)
    emails = build_emails(customers, timelines, rng, today)
    tasks = build_simple_activities(customers, timelines, rng, today, "task", 5)
    phonecalls = build_simple_activities(customers, timelines, rng, today, "phonecall", 3)
    appointments = build_simple_activities(customers, timelines, rng, today, "appointment", 1)
    # annotation entityset is "annotations"; uses objectid (not regardingobject)
    annotations = []  # Lightweight - skip for brevity in this revision

    print("[gen] incidents + agent decisions ...", file=sys.stderr)
    incidents = build_incidents(timelines, rng)
    agent_decisions = build_agent_decisions(customers, timelines, rng)

    print("[gen] supporting customer tables ...", file=sys.stderr)
    products = build_products(rng)
    pricelevels = build_pricelevels()
    accounts = build_account_records(customers, today)
    contact_records = build_contact_records(customers, rng, faker)
    address_records = build_address_records(customers, rng)
    comm_prefs = build_communication_preference(customers)
    sepa_mandates = build_sepa_mandates(customers, rng, today)
    ltv_snapshots = build_lifetime_value_snapshots(customers, today, rng)
    delivery_notes = build_delivery_notes(timelines, orders_by_invoice, rng)

    bundles = {
        "pricelevel": pricelevels,
        "product": products,
        "account": accounts,
        "contact": contact_records,
        "customeraddress": address_records,
        f"{PREFIX}_communicationpreference": comm_prefs,
        f"{PREFIX}_sepamandate": sepa_mandates,
        f"{PREFIX}_lifetimevaluesnapshot": ltv_snapshots,
        "salesorder": orders,
        "salesorderdetail": order_lines,
        f"{PREFIX}_deliverynote": delivery_notes,
        "invoice": invoices,
        "invoicedetail": invoice_lines,
        f"{PREFIX}_paymentevent": payment_events,
        f"{PREFIX}_paymentplan": payment_plans,
        f"{PREFIX}_paymentplaninstallment": plan_installments,
        "contract": contracts,
        "contractdetail": contract_lines,
        f"{PREFIX}_creditsignal": credit_signals,
        f"{PREFIX}_riskscoresnapshot": risk_snapshots,
        f"{PREFIX}_churnsignal": churn_signals,
        f"{PREFIX}_dunningevent": dunning,
        "letter": letters,
        "email": emails,
        "task": tasks,
        "phonecall": phonecalls,
        "appointment": appointments,
        "annotation": annotations,
        "incident": incidents,
        f"{PREFIX}_agentdecision": agent_decisions,
    }

    total_rows = sum(len(r) for r in bundles.values())

    # Story-pattern verification
    print("\n=== Story-pattern verification ===", file=sys.stderr)
    pattern_results = verify_story_patterns(customers, timelines, dunning, payment_plans,
                                              credit_signals, incidents, letters)
    all_ok = True
    for label, ok in pattern_results:
        print(f"  {'OK ' if ok else 'FAIL'}  {label}", file=sys.stderr)
        if not ok:
            all_ok = False

    print(f"\n[gen] Total rows generated: {total_rows:,}", file=sys.stderr)
    for table, recs in bundles.items():
        print(f"   {table:50s} {len(recs):>8,}", file=sys.stderr)

    # Write preview JSON files (always, even in --apply mode for audit)
    preview_dir = Path(args.preview_dir)
    print(f"\n[preview] writing JSON to {preview_dir} ...", file=sys.stderr)
    for table_logical, key in INSERT_ORDER:
        write_preview(preview_dir, key, bundles.get(table_logical, []))
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "customers": args.customers,
        "months": args.months,
        "total_rows": total_rows,
        "rows_per_table": {t: len(r) for t, r in bundles.items()},
        "story_patterns_all_passed": all_ok,
        "story_pattern_results": [{"label": l, "ok": ok} for l, ok in pattern_results],
    }
    (preview_dir / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")

    if dry_run:
        print(f"\n=== Dry-run complete ===", file=sys.stderr)
        print(f"Total rows planned:  {total_rows:,}", file=sys.stderr)
        print(f"Preview written to:  {preview_dir}", file=sys.stderr)
        print(f"Story patterns:      {'ALL PASSED' if all_ok else 'SOME FAILED - investigate before --apply'}",
              file=sys.stderr)
        if total_rows > LARGE_THRESHOLD:
            print(f"Run with --apply --confirm-large to insert (>{LARGE_THRESHOLD:,} rows)", file=sys.stderr)
        else:
            print(f"Run with --apply to insert", file=sys.stderr)
        return

    # ---------- --apply: actually insert ----------
    if not all_ok:
        sys.exit("Story-pattern verification failed; aborting --apply. Fix generators first.")
    if total_rows > LARGE_THRESHOLD and not args.confirm_large:
        sys.exit(f"--apply with > {LARGE_THRESHOLD:,} rows requires --confirm-large "
                 f"(planned: {total_rows:,}).")

    print(f"\n[auth] env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    # Pre-flight: warn if mueller_* records already exist (and --resume not passed)
    if not args.resume:
        body = client.get(f"{PREFIX}_paymentevents",
                          params={"$top": "1", "$select": f"{PREFIX}_paymenteventid"},
                          allow_404=True)
        if body and body.get("value"):
            sys.exit("Detected existing mueller_paymentevent rows. "
                     "Pass --resume to continue from local state, or --replace to wipe (not implemented).")

    # Pre-flight for products: fetch default Unit Group + Unit, patch records,
    # and run a single-product verification probe before the bulk batch loop.
    if not state.is_done("product") and bundles.get("product"):
        schedule_id, uom_id = fetch_unit_defaults(client)
        patch_products_with_uom_defaults(bundles["product"], schedule_id, uom_id)
        _run_single_record_probe(client, state, "product", "products",
                                  bundles["product"])

    # Pre-flight for customeraddress: addressnumber 1/2 are reserved by
    # Dataverse, so the generator now starts at 3. Probe one record to confirm.
    if not state.is_done("customeraddress") and bundles.get("customeraddress"):
        _run_single_record_probe(client, state, "customeraddress",
                                  "customeraddresses", bundles["customeraddress"])

    # Pre-flight for mueller_lifetimevaluesnapshot: snapshot's mueller_segment
    # OptionSet has only 4 options vs account's 5. Probe to validate the mapping.
    if (not state.is_done(f"{PREFIX}_lifetimevaluesnapshot")
            and bundles.get(f"{PREFIX}_lifetimevaluesnapshot")):
        _run_single_record_probe(
            client, state, f"{PREFIX}_lifetimevaluesnapshot",
            f"{PREFIX}_lifetimevaluesnapshots",
            bundles[f"{PREFIX}_lifetimevaluesnapshot"])

    # Orphan recovery for tables with unique business-key alternate keys.
    # Without this, a re-run after a partial POST that wasn't checkpointed
    # to id_map hits 0x80040237 ("matching key values already exists").
    if not state.is_done("salesorder"):
        hydrate_orphans_by_business_key(
            client, state, "salesorder", "salesorders",
            "salesorderid", "ordernumber", bundles["salesorder"], "ORD-")
    if not state.is_done("invoice"):
        hydrate_orphans_by_business_key(
            client, state, "invoice", "invoices",
            "invoiceid", "invoicenumber", bundles["invoice"], "INV-")
    if not state.is_done("contract"):
        hydrate_orphans_by_business_key(
            client, state, "contract", "contracts",
            "contractid", "contractnumber", bundles["contract"], "CON-")

    print(f"\n=== Insert pipeline ({mode}) ===", file=sys.stderr)
    overall_inserted = 0
    overall_skipped = 0
    for table_logical, _key in INSERT_ORDER:
        if state.is_done(table_logical):
            print(f"[{table_logical}] already complete - skip", file=sys.stderr)
            continue
        recs = bundles.get(table_logical, [])
        if not recs:
            state.mark_table_done(table_logical)
            continue
        before = len(state.id_map)
        inserted = insert_table(client, state, table_logical, recs, dry_run=False,
                                  batch_size=args.batch_size)
        overall_inserted += inserted
        overall_skipped += (len(recs) - inserted)
        state.mark_table_done(table_logical)
        print(f"  table {table_logical} done (cumulative inserted={overall_inserted})", file=sys.stderr)

    print(f"\n=== Apply complete ===", file=sys.stderr)
    print(f"Total inserted: {overall_inserted:,}", file=sys.stderr)
    print(f"Total skipped (already mapped): {overall_skipped:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
