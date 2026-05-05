"""Unified configuration for the Müller AR-agent toolkit.

All scripts (`export_metadata`, `provision_schema`, `generate_synthetic_data`,
`seed_invoices`, `list_open_invoices`, `build_shortlist`) call
:func:`load_config` once at startup. Required values come from environment
variables; a `.env` file at the repository root is loaded as a convenience
fallback. Missing values fail loudly with a precise message.

Required environment variables
------------------------------
- ``DATAVERSE_TENANT_ID``        — Entra ID directory (tenant) GUID
- ``DATAVERSE_CLIENT_ID``        — App-registration client (application) ID
- ``DATAVERSE_CLIENT_SECRET``    — Client secret value
- ``DATAVERSE_ENVIRONMENT_URL``  — Dataverse host (``yourorg.crm16.dynamics.com``)

Optional overrides
------------------
- ``KI_FM_DATA_DIR``         — defaults to ``<repo>/data``
- ``KI_FM_OUTPUT_DIR``       — defaults to ``<repo>/output``
- ``KI_FM_MAX_WORKERS``      — default 4
- ``KI_FM_MAX_RETRIES``      — default 8
- ``KI_FM_CHECKPOINT_EVERY`` — default 25
- ``KI_FM_BATCH_SIZE``       — default 100
- ``KI_FM_REQUEST_TIMEOUT``  — default 60 (seconds)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DataverseConfig:
    """Connection details for a Dataverse environment + Entra ID app."""

    tenant_id: str
    client_id: str
    client_secret: str
    environment_url: str
    api_version: str = "v9.2"

    @property
    def base_url(self) -> str:
        return f"https://{self.environment_url}/api/data/{self.api_version}/"

    @property
    def resource(self) -> str:
        return f"https://{self.environment_url}"

    @property
    def scope(self) -> str:
        return f"https://{self.environment_url}/.default"

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


@dataclass(frozen=True)
class Paths:
    """Filesystem layout. All paths are absolute and platform-neutral."""

    repo_root: Path
    data_dir: Path
    output_dir: Path
    preview_dir: Path
    metadata_file: Path
    metadata_partial_file: Path
    shortlist_file: Path
    id_map_file: Path
    progress_file: Path


@dataclass(frozen=True)
class Limits:
    """Throughput / retry knobs shared by every Dataverse-touching script."""

    max_workers: int = 4
    max_retries: int = 8
    checkpoint_every: int = 25
    batch_size: int = 100
    request_timeout_seconds: int = 60


def load_dotenv(path: Path | None = None) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` file.

    Pre-existing environment variables always win — values from the file are
    only set when the variable is not already defined.
    """
    candidates: list[Path]
    if path is not None:
        candidates = [path]
    else:
        candidates = [REPO_ROOT / ".env", Path.cwd() / ".env"]

    for candidate in candidates:
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        # Stop after the first file we read to avoid surprising overrides
        return


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name!r}. "
            f"Set it in your shell or in a .env file at the repository root. "
            f"See .env.example for the full list."
        )
    return value


def _normalize_environment_url(raw: str) -> str:
    """Accept ``https://...``, trailing slashes, etc. Return bare host."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.rstrip("/")
    return cleaned


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer (got {raw!r})") from exc


def load_config(dotenv_path: Path | None = None) -> Tuple[DataverseConfig, Paths, Limits]:
    """Load Dataverse credentials, paths, and limits in one call."""
    load_dotenv(dotenv_path)

    cfg = DataverseConfig(
        tenant_id=_require("DATAVERSE_TENANT_ID"),
        client_id=_require("DATAVERSE_CLIENT_ID"),
        client_secret=_require("DATAVERSE_CLIENT_SECRET"),
        environment_url=_normalize_environment_url(_require("DATAVERSE_ENVIRONMENT_URL")),
        api_version=os.environ.get("DATAVERSE_API_VERSION", "v9.2"),
    )

    data_dir = Path(os.environ.get("KI_FM_DATA_DIR", str(REPO_ROOT / "data"))).resolve()
    output_dir = Path(os.environ.get("KI_FM_OUTPUT_DIR", str(REPO_ROOT / "output"))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = Paths(
        repo_root=REPO_ROOT,
        data_dir=data_dir,
        output_dir=output_dir,
        preview_dir=output_dir / "synthetic_data_preview",
        metadata_file=output_dir / "dataverse_metadata.json",
        metadata_partial_file=output_dir / "dataverse_metadata.partial.json",
        shortlist_file=data_dir / "scenario_table_shortlist.json",
        id_map_file=output_dir / "synthetic_id_map.json",
        progress_file=output_dir / "synthetic_data_progress.json",
    )

    limits = Limits(
        max_workers=_int_env("KI_FM_MAX_WORKERS", 4),
        max_retries=_int_env("KI_FM_MAX_RETRIES", 8),
        checkpoint_every=_int_env("KI_FM_CHECKPOINT_EVERY", 25),
        batch_size=_int_env("KI_FM_BATCH_SIZE", 100),
        request_timeout_seconds=_int_env("KI_FM_REQUEST_TIMEOUT", 60),
    )

    return cfg, paths, limits
