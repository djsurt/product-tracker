"""Meshy image-to-3D client (Phase 10).

All provider knowledge lives here so a vendor swap (Tripo etc.) touches one
file. Endpoints per https://docs.meshy.ai/en/api/image-to-3d.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import httpx

from core.settings import get_settings


class MeshyError(Exception):
    """Provider rejected the request or returned an unusable payload."""


@dataclass
class MeshyTask:
    status: str  # PENDING | IN_PROGRESS | SUCCEEDED | FAILED | CANCELED
    model_urls: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _api_key() -> str:
    key = get_settings().meshy_api_key
    if not key:
        raise MeshyError("MESHY_API_KEY is not configured")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def _base() -> str:
    return get_settings().meshy_base_url.rstrip("/")


def create_image_to_3d_task(image_url: str, client: httpx.Client | None = None) -> str:
    c = client or httpx.Client(timeout=30)
    resp = c.post(
        f"{_base()}/openapi/v1/image-to-3d",
        headers=_headers(),
        json={
            "image_url": image_url,
            "enable_pbr": False,
            # usdz isn't in the default export set; ask for both up front.
            "target_formats": ["glb", "usdz"],
        },
    )
    if resp.status_code != 200:
        raise MeshyError(f"create failed: {resp.status_code} {resp.text[:200]}")
    task_id = resp.json().get("result")
    if not task_id:
        raise MeshyError(f"create returned no task id: {resp.text[:200]}")
    return task_id


def get_task(task_id: str, client: httpx.Client | None = None) -> MeshyTask:
    c = client or httpx.Client(timeout=30)
    resp = c.get(f"{_base()}/openapi/v1/image-to-3d/{task_id}", headers=_headers())
    if resp.status_code != 200:
        raise MeshyError(f"poll failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    return MeshyTask(
        status=data.get("status", "FAILED"),
        model_urls=data.get("model_urls") or {},
        error=(data.get("task_error") or {}).get("message"),
    )


def download_file(
    url: str, dest: pathlib.Path, client: httpx.Client | None = None
) -> None:
    c = client or httpx.Client(timeout=120, follow_redirects=True)
    resp = c.get(url)
    if resp.status_code != 200:
        raise MeshyError(f"download failed: {resp.status_code}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
