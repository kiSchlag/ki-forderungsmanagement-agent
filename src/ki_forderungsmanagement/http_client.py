"""Shared MSAL auth + Dataverse Web API client.

Implements the OAuth2 client-credentials flow against Microsoft Entra ID and
provides a thin :class:`DataverseClient` over ``requests.Session`` with
Retry-After-aware throttling, exponential backoff on 5xx and network errors,
and ``@odata.nextLink`` pagination. Write methods (``post``, ``patch``,
``delete``) share the same retry policy.

Microsoft's official guidance is to start low and let the server tell you what
it can handle via ``Retry-After``. See:
https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits
"""

from __future__ import annotations

import random
import sys
import time
from typing import Any, Dict, Iterator, List, Optional

import msal
import requests

from .config import DataverseConfig, Limits


def acquire_token(cfg: DataverseConfig) -> str:
    """Run the OAuth2 client-credentials flow and return the bearer token."""
    app = msal.ConfidentialClientApplication(
        client_id=cfg.client_id,
        client_credential=cfg.client_secret,
        authority=cfg.authority,
    )
    result = app.acquire_token_for_client(scopes=[cfg.scope])
    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication against {cfg.authority} failed: "
            f"{result.get('error_description', result)}"
        )
    return result["access_token"]


class DataverseClient:
    """Minimal Web API client.

    Builds OData v4 requests with bearer auth headers. ``get`` retries on 429
    (honouring ``Retry-After``) and on 5xx / network errors with exponential
    backoff plus jitter. ``get_all`` follows ``@odata.nextLink`` until exhaustion.
    """

    def __init__(self, cfg: DataverseConfig, limits: Limits, token: Optional[str] = None):
        self.cfg = cfg
        self.limits = limits
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token or acquire_token(cfg)}",
            "Accept": "application/json",
            "OData-Version": "4.0",
            "OData-MaxVersion": "4.0",
        })

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return self.cfg.base_url + path_or_url.lstrip("/")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(
        self,
        path_or_url: str,
        params: Optional[Dict[str, str]] = None,
        allow_404: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Issue a GET. Returns the parsed JSON body.

        When ``allow_404=True`` a 404 returns ``None`` instead of raising —
        useful for idempotency probes ("does this metadata object exist?").
        """
        url = self._url(path_or_url)
        last_error = ""
        for attempt in range(self.limits.max_retries):
            try:
                response = self.session.get(
                    url, params=params, timeout=self.limits.request_timeout_seconds
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"network error: {exc}"
                self._sleep_backoff(attempt, last_error)
                continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "30"))
                print(
                    f"  ! 429 throttled -> sleep {retry_after}s "
                    f"(attempt {attempt + 1}/{self.limits.max_retries})",
                    file=sys.stderr,
                )
                time.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                last_error = f"{response.status_code} {response.reason}"
                self._sleep_backoff(attempt, last_error)
                continue

            if response.status_code == 404 and allow_404:
                return None

            if not response.ok:
                raise RuntimeError(
                    f"GET {response.url} -> {response.status_code} {response.reason}\n"
                    f"{response.text[:1500]}"
                )
            return response.json() if response.content else {}

        raise RuntimeError(
            f"GET {url} failed after {self.limits.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def get_all(self, path: str, params: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = self.get(path, params=params)
        items.extend(page.get("value", []))
        while "@odata.nextLink" in page:
            page = self.get(page["@odata.nextLink"])
            items.extend(page.get("value", []))
        return items

    def iter_pages(
        self, path: str, params: Optional[Dict[str, str]] = None
    ) -> Iterator[Dict[str, Any]]:
        """Yield each value-row, transparently following pagination."""
        page = self.get(path, params=params)
        for item in page.get("value", []):
            yield item
        while "@odata.nextLink" in page:
            page = self.get(page["@odata.nextLink"])
            for item in page.get("value", []):
                yield item

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def post(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        return self._write("POST", path, json=json, extra_headers=extra_headers)

    def patch(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        return self._write("PATCH", path, json=json, extra_headers=extra_headers)

    def delete(
        self,
        path: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        return self._write("DELETE", path, json=None, extra_headers=extra_headers)

    def _write(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]],
        extra_headers: Optional[Dict[str, str]],
    ) -> requests.Response:
        url = self._url(path)
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        last_error = ""
        for attempt in range(self.limits.max_retries):
            try:
                response = self.session.request(
                    method,
                    url,
                    json=json,
                    headers=headers,
                    timeout=self.limits.request_timeout_seconds,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"network error: {exc}"
                self._sleep_backoff(attempt, last_error)
                continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "30"))
                print(
                    f"  ! 429 throttled -> sleep {retry_after}s "
                    f"(attempt {attempt + 1}/{self.limits.max_retries})",
                    file=sys.stderr,
                )
                time.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                last_error = f"{response.status_code} {response.reason}"
                self._sleep_backoff(attempt, last_error)
                continue

            if not response.ok:
                raise RuntimeError(
                    f"{method} {url} -> {response.status_code} {response.reason}\n"
                    f"{response.text[:1500]}"
                )
            return response

        raise RuntimeError(
            f"{method} {url} failed after {self.limits.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # $batch (multipart write)
    # ------------------------------------------------------------------

    def batch_create(self, ops: List[Dict[str, Any]]) -> List[str]:
        """Send up to ~100 single-create operations as one ``$batch`` ChangeSet.

        Each ``op`` is ``{"entityset": "<plural>", "body": {...}}``. Returns
        the list of created GUIDs in submission order.
        See https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api
        """
        import json as _json
        import re as _re
        import uuid as _uuid

        if not ops:
            return []

        boundary = f"batch_{_uuid.uuid4().hex}"
        cs_boundary = f"changeset_{_uuid.uuid4().hex}"
        crlf = "\r\n"

        lines: List[str] = [
            f"--{boundary}",
            f"Content-Type: multipart/mixed; boundary={cs_boundary}",
            "",
        ]
        for i, op in enumerate(ops, start=1):
            lines += [
                f"--{cs_boundary}",
                "Content-Type: application/http",
                "Content-Transfer-Encoding: binary",
                f"Content-ID: {i}",
                "",
                f"POST /api/data/{self.cfg.api_version}/{op['entityset']} HTTP/1.1",
                "Content-Type: application/json; type=entry",
                "",
                _json.dumps(op["body"], ensure_ascii=False),
            ]
        lines += [f"--{cs_boundary}--", f"--{boundary}--"]
        body = crlf.join(lines)

        url = self.cfg.base_url + "$batch"
        headers = {
            "Content-Type": f'multipart/mixed; boundary="{boundary}"',
            "Accept": "application/json",
            "OData-Version": "4.0",
            "OData-MaxVersion": "4.0",
        }

        response = None
        for attempt in range(self.limits.max_retries):
            response = self.session.post(
                url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=max(180, self.limits.request_timeout_seconds),
            )
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "30"))
                print(
                    f"  ! 429 throttled -> sleep {retry_after}s "
                    f"(attempt {attempt + 1}/{self.limits.max_retries})",
                    file=sys.stderr,
                )
                time.sleep(retry_after)
                continue
            if 500 <= response.status_code < 600:
                self._sleep_backoff(attempt, f"{response.status_code} {response.reason}")
                continue
            if not response.ok:
                raise RuntimeError(
                    f"$batch -> {response.status_code} {response.reason}\n"
                    f"{response.text[:2000]}"
                )
            break
        else:
            raise RuntimeError(f"$batch failed after {self.limits.max_retries} retries")

        guids = _re.findall(
            r"OData-EntityId:\s*[^\s]+\(([0-9a-fA-F-]{36})\)",
            response.text,
        )
        if len(guids) != len(ops):
            err = _re.search(r'"error":\s*\{[^}]+\}', response.text)
            err_msg = err.group(0)[:1500] if err else response.text[:1500]
            raise RuntimeError(
                f"$batch returned {len(guids)} GUIDs, expected {len(ops)}\n{err_msg}"
            )
        return [g.lower() for g in guids]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        delay = min(2 ** attempt, 60) + random.uniform(0, 1)
        print(
            f"  ! {reason} -> retry in {delay:.1f}s "
            f"(attempt {attempt + 1}/{self.limits.max_retries})",
            file=sys.stderr,
        )
        time.sleep(delay)
