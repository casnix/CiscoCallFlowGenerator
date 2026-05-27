#!/usr/bin/env python3
"""
main.py — FastAPI backend for the CUPI Call Flow Viewer
--------------------------------------------------------
Proxies requests to Cisco Unity Connection's CUPI REST API,
resolving handler names and returning structured JSON to the frontend.

Run:
    pip install fastapi uvicorn requests
    uvicorn main:app --reload --port 8000

Then open http://localhost:8000 in your browser.
"""

import urllib3
from functools import lru_cache
from typing import Optional

import requests as req
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="CUPI Call Flow Viewer")

ACTION_LABELS = {
    "0": "Ignore",
    "1": "Take message",
    "2": "Go to handler",
    "3": "Skip greeting",
    "4": "Restart greeting",
    "5": "Transfer to number",
    "6": "Go to conversation",
    "7": "Transfer to alt ext",
}


# ---------------------------------------------------------------------------
# CUPI client (stateless per-request, credentials passed each time)
# ---------------------------------------------------------------------------

def cupi_get(host: str, user: str, password: str, path: str) -> dict:
    url = f"https://{host}/vmrest{path}"
    try:
        resp = req.get(
            url,
            auth=(user, password),
            headers={"Accept": "application/json"},
            verify=False,
            timeout=15,
        )
    except req.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot reach {host} — check hostname and network.")
    except req.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Request to {host} timed out.")

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Authentication failed — check username and password.")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"CUPI endpoint not found: {path}")
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:200])

    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Server returned non-JSON response.")


def normalise_list(data: dict, *keys) -> list[dict]:
    """Try multiple key names and always return a list."""
    for key in keys:
        val = data.get(key)
        if val is not None:
            return [val] if isinstance(val, dict) else val
    return []


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class Credentials(BaseModel):
    host: str
    user: str
    password: str


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h2>index.html not found — place it alongside main.py</h2>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/handlers")
def get_handlers(
    host: str = Query(...),
    user: str = Query(...),
    password: str = Query(...),
):
    data = cupi_get(host, user, password, "/handlers/callhandlers")
    handlers = normalise_list(data, "Callhandler", "CallHandler", "callhandler")
    result = [
        {"objectId": h.get("ObjectId", ""), "displayName": h.get("DisplayName", "Unnamed")}
        for h in handlers
        if h.get("ObjectId")
    ]
    result.sort(key=lambda h: h["displayName"].lower())
    return JSONResponse(result)


@app.get("/api/flow")
def get_flow(
    host: str = Query(...),
    user: str = Query(...),
    password: str = Query(...),
    handler_oid: str = Query(...),
):
    # Fetch menu entries
    data = cupi_get(host, user, password, f"/handlers/callhandlers/{handler_oid}/menuentries")
    entries = normalise_list(data, "MenuEntry", "MenuItem", "menuentry")

    # Collect all target handler OIDs we need to resolve
    target_oids = set()
    for e in entries:
        if str(e.get("Action", "")) == "2" and e.get("TargetHandlerObjectId"):
            target_oids.add(e["TargetHandlerObjectId"])

    # Resolve handler names (best-effort)
    handler_names: dict[str, str] = {}
    for oid in target_oids:
        try:
            hdata = cupi_get(host, user, password, f"/handlers/callhandlers/{oid}")
            name = hdata.get("DisplayName") or \
                   normalise_list(hdata, "Callhandler", "CallHandler")[0].get("DisplayName", oid) \
                   if normalise_list(hdata, "Callhandler", "CallHandler") else oid
            handler_names[oid] = name
        except Exception:
            handler_names[oid] = oid

    # Build structured response
    result = []
    for e in entries:
        action_code = str(e.get("Action", "0"))
        action_label = ACTION_LABELS.get(action_code, f"Action {action_code}")
        target_oid = e.get("TargetHandlerObjectId", "")

        entry = {
            "key": e.get("TouchtoneKey", "?"),
            "action": action_code,
            "actionLabel": action_label,
            "locked": e.get("Locked", "false") == "true",
            "transferNumber": e.get("TransferNumber", ""),
            "targetConversation": e.get("TargetConversation", ""),
            "targetHandlerOid": target_oid,
            "targetHandlerName": handler_names.get(target_oid, "") if target_oid else "",
        }
        result.append(entry)

    # Sort: digits first (0-9), then * then #
    def key_sort(e):
        k = str(e["key"])
        if k.isdigit():
            return (0, int(k))
        return (1, k)

    result.sort(key=key_sort)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
    