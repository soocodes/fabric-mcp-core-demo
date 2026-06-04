"""
fabric_client.py
-----------------
Thin wrapper around the Microsoft Fabric REST API (https://api.fabric.microsoft.com/v1).

Auth strategy (no secrets stored):
  Acquires a bearer token by shelling out to Azure PowerShell:
      Get-AzAccessToken -ResourceUrl https://api.fabric.microsoft.com
  This reuses the interactive `Connect-AzAccount` session already present on the machine.

The client handles:
  * GET / POST / DELETE helpers with auth header
  * Long Running Operation (LRO) polling (202 + Location / Retry-After)
  * Convenience helpers: create_item, get_item_by_name, run_notebook_job

Docs:
  * Item management:  https://learn.microsoft.com/rest/api/fabric/articles/item-management/item-management-overview
  * Create item:      https://learn.microsoft.com/rest/api/fabric/core/items/create-item
  * LRO:              https://learn.microsoft.com/rest/api/fabric/articles/long-running-operation
"""

import base64
import json
import subprocess
import time
from pathlib import Path

import requests

FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
RESOURCE = "https://api.fabric.microsoft.com"


def get_token(resource: str = RESOURCE) -> str:
    """Get a bearer token for `resource` from the local Az PowerShell session."""
    ps = (
        "$ErrorActionPreference='Stop';"
        f"(Get-AzAccessToken -ResourceUrl {resource}).Token"
    )
    for exe in ("pwsh", "powershell"):
        try:
            out = subprocess.run(
                [exe, "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=120,
            )
            token = out.stdout.strip()
            if token and "." in token:
                return token
        except FileNotFoundError:
            continue
    raise RuntimeError(
        "Could not acquire a Fabric token. Run 'Connect-AzAccount' first "
        "(Az PowerShell) or set FABRIC_TOKEN env var."
    )


class FabricClient:
    def __init__(self, token: str | None = None):
        self.token = token or get_token()
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    # ---- low level -------------------------------------------------------
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{FABRIC_BASE}{path}"

    def get(self, path: str, **kw):
        return self.s.get(self._url(path), **kw)

    def post(self, path: str, json_body=None, **kw):
        return self.s.post(self._url(path), data=json.dumps(json_body) if json_body is not None else None, **kw)

    def delete(self, path: str, **kw):
        return self.s.delete(self._url(path), **kw)

    # ---- LRO -------------------------------------------------------------
    def poll_lro(self, resp, timeout=900, interval=5):
        """Given a 202 response, poll the operation until it completes.
        Returns the final operation result (or the result payload if available)."""
        if resp.status_code != 202:
            return resp
        op_url = resp.headers.get("Location")
        retry = int(resp.headers.get("Retry-After", interval) or interval)
        if not op_url:
            return resp
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(retry)
            st = self.s.get(op_url)
            if st.status_code not in (200, 202):
                st.raise_for_status()
            body = st.json() if st.text else {}
            status = body.get("status", "")
            if status in ("Succeeded", "Completed"):
                # fetch result if a result endpoint is offered
                result_url = st.headers.get("Location") or f"{op_url}/result"
                r = self.s.get(result_url)
                return r if r.status_code == 200 else st
            if status in ("Failed", "Cancelled"):
                raise RuntimeError(f"Operation {status}: {json.dumps(body)}")
            retry = int(st.headers.get("Retry-After", retry) or retry)
        raise TimeoutError("LRO polling timed out")

    # ---- items -----------------------------------------------------------
    def list_items(self, ws: str, item_type: str | None = None):
        path = f"/workspaces/{ws}/items"
        if item_type:
            path += f"?type={item_type}"
        r = self.get(path)
        r.raise_for_status()
        return r.json().get("value", [])

    def get_item_by_name(self, ws: str, display_name: str, item_type: str | None = None):
        for it in self.list_items(ws, item_type):
            if it.get("displayName") == display_name:
                return it
        return None

    def create_item(self, ws: str, display_name: str, item_type: str,
                    definition: dict | None = None, description: str | None = None,
                    skip_if_exists: bool = True):
        """Create a Fabric item; returns the item object. Idempotent by display name."""
        if skip_if_exists:
            existing = self.get_item_by_name(ws, display_name, item_type)
            if existing:
                print(f"  = {item_type} '{display_name}' already exists ({existing['id']})")
                return existing
        body = {"displayName": display_name, "type": item_type}
        if description:
            body["description"] = description
        if definition:
            body["definition"] = definition
        r = self.post(f"/workspaces/{ws}/items", body)
        if r.status_code == 202:
            res = self.poll_lro(r)
            item = res.json() if (res is not None and res.text) else {}
        elif r.status_code in (200, 201):
            item = r.json()
        else:
            raise RuntimeError(f"Create {item_type} '{display_name}' failed: {r.status_code} {r.text}")
        if not isinstance(item, dict) or "id" not in item:
            item = self.get_item_by_name(ws, display_name, item_type) or item
        print(f"  + {item_type} '{display_name}' created ({item.get('id')})")
        return item

    # ---- update item definition (in place) -------------------------------
    def update_item_definition(self, ws: str, item_id: str, definition: dict,
                               update_metadata: bool = False):
        """Update an existing item's definition in place (keeps the same id)."""
        q = "?updateMetadata=true" if update_metadata else ""
        r = self.post(f"/workspaces/{ws}/items/{item_id}/updateDefinition{q}",
                      {"definition": definition})
        if r.status_code == 202:
            self.poll_lro(r)
        elif r.status_code not in (200, 201):
            raise RuntimeError(f"Update definition failed: {r.status_code} {r.text}")
        return item_id


    # ---- notebook job ----------------------------------------------------
    def run_notebook(self, ws: str, notebook_id: str, timeout=1800):
        r = self.post(f"/workspaces/{ws}/items/{notebook_id}/jobs/instances?jobType=RunNotebook", {})
        if r.status_code not in (200, 202):
            raise RuntimeError(f"Run notebook failed: {r.status_code} {r.text}")
        loc = r.headers.get("Location")
        if not loc:
            return r
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(10)
            st = self.s.get(loc)
            body = st.json() if st.text else {}
            status = body.get("status")
            print(f"    notebook job status: {status}")
            if status == "Completed":
                return body
            if status in ("Failed", "Cancelled", "Deduped"):
                raise RuntimeError(f"Notebook job {status}: {json.dumps(body)}")
        raise TimeoutError("Notebook job timed out")


# ---- definition helpers ---------------------------------------------------
def part(path: str, payload: str) -> dict:
    """Build an inline base64 definition part from a string payload."""
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return {"path": path, "payload": b64, "payloadType": "InlineBase64"}


def definition_from_folder(folder: Path, fmt: str | None = None) -> dict:
    """Build an item definition by base64-encoding every file under `folder`."""
    parts = []
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.name != ".platform":
            rel = f.relative_to(folder).as_posix()
            parts.append(part(rel, f.read_text(encoding="utf-8")))
    d = {"parts": parts}
    if fmt:
        d["format"] = fmt
    return d
