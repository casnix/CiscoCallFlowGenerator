#!/usr/bin/env python3
"""
cupi_call_flow.py
-----------------
Connects to a Cisco Unity Connection server via CUPI (REST API),
retrieves call handler menu entries, and outputs a visual call flow.

Usage:
    python cupi_call_flow.py --host 192.168.1.100 --user admin --password Secret1
    python cupi_call_flow.py --host cuc.company.com --user admin --password Secret1 --output html
    python cupi_call_flow.py --host cuc.company.com --user admin --password Secret1 --handler "Main Menu"

Requirements:
    pip install requests
"""

import sys
import json
import urllib3
import argparse
import requests

from getpass import getpass
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ACTION_LABELS = {
    "0": "Ignore",
    "1": "Take message",
    "2": "Go to handler",
    "3": "Skip greeting",
    "4": "Restart greeting",
    "5": "Transfer to number",
    "6": "Go to conversation",
    "7": "Hang up",
}

COLORS_ANSI = {
    "1": "\033[92m",   # green  - take message
    "2": "\033[94m",   # blue   - go to handler
    "5": "\033[93m",   # yellow - transfer
    "6": "\033[94m",   # blue   - go to conversation
    "7": "\033[91m",   # red    - hang up
    "reset": "\033[0m",
    "bold":  "\033[1m",
    "dim":   "\033[2m",
}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

class CUPIClient:
    def __init__(self, host: str, username: str, password: str):
        self.base = f"https://{host}/vmrest"
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})
        self.session.verify = False  # CUC typically uses a self-signed cert

    def get(self, path: str) -> dict:
        url = f"{self.base}{path}"
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_call_handlers(self) -> list[dict]:
        data = self.get("/handlers/callhandlers")
        handlers = data.get("CallHandler", [])
        if isinstance(handlers, dict):
            handlers = [handlers]
        return handlers

    def get_menu_entries(self, object_id: str) -> list[dict]:
        data = self.get(f"/handlers/callhandlers/{object_id}/menuentries")
        entries = data.get("MenuItem", [])
        if isinstance(entries, dict):
            entries = [entries]
        return entries

    def search_handler(self, name: str) -> list[dict]:
        import urllib.parse
        query = urllib.parse.quote(f"(DisplayName is {name})")
        data = self.get(f"/handlers/callhandlers?query={query}")
        handlers = data.get("CallHandler", [])
        if isinstance(handlers, dict):
            handlers = [handlers]
        return handlers


# ---------------------------------------------------------------------------
# ASCII flow renderer
# ---------------------------------------------------------------------------

def render_ascii(handler_name: str, entries: list[dict]) -> str:
    B = COLORS_ANSI["bold"]
    D = COLORS_ANSI["dim"]
    R = COLORS_ANSI["reset"]

    lines = []
    width = 60
    bar = "─" * width

    lines.append(f"\n{B}{'─'*width}{R}")
    lines.append(f"{B}  CALL FLOW: {handler_name}{R}")
    lines.append(f"{B}{'─'*width}{R}")

    # Root node
    lines.append(f"\n        ┌{'─'*30}┐")
    name_trunc = handler_name[:28].center(30)
    lines.append(f"        │{B}{name_trunc}{R}│")
    lines.append(f"        │{'  [Call Handler]':^30}│")
    lines.append(f"        └{'─'*15}┬{'─'*14}┘")
    lines.append(f"                 │")

    active = [e for e in entries if e.get("Action") not in ("0", None)]
    ignored = [e for e in entries if e.get("Action") == "0"]

    if not active:
        lines.append(f"        {D}(no active menu keys configured){R}\n")
        return "\n".join(lines)

    # Branch lines
    keys_str = "  ┌" + "".join(f"───[{e.get('TouchtoneKey','?')}]─" for e in active) + "┐"
    lines.append(f"  ┌{'─'*56}┐")
    lines.append(f"  │{'Keys: ' + ', '.join(str(e.get('TouchtoneKey','?')) for e in active):^56}│")
    lines.append(f"  └{'─'*27}┬{'─'*27}┘")
    lines.append(f"                           │")
    lines.append("")

    for entry in active:
        key = entry.get("TouchtoneKey", "?")
        action_code = str(entry.get("Action", ""))
        action_label = ACTION_LABELS.get(action_code, f"Action {action_code}")
        color = COLORS_ANSI.get(action_code, "")

        dest_parts = []
        if entry.get("TransferNumber"):
            dest_parts.append(f"Number: {entry['TransferNumber']}")
        if entry.get("TargetHandlerObjectId"):
            dest_parts.append(f"Handler OID: {entry['TargetHandlerObjectId'][:16]}…")
        if entry.get("TargetConversation"):
            dest_parts.append(f"Conversation: {entry['TargetConversation']}")
        dest = ", ".join(dest_parts) if dest_parts else ""

        locked = " [locked]" if entry.get("Locked") == "true" else ""
        lines.append(f"  Press [{B}{key}{R}]  →  {color}{action_label}{R}{D}{locked}{R}")
        if dest:
            lines.append(f"           {D}└─ {dest}{R}")
        lines.append("")

    if ignored:
        lines.append(f"{D}  Keys ignored: {', '.join(str(e.get('TouchtoneKey','?')) for e in ignored)}{R}")

    lines.append(f"{D}{'─'*width}{R}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML flow renderer
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Call Flow: {handler_name}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #f4f5f7; color: #1a1a2e; padding: 2rem; }}
  h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 0.25rem; }}
  .sub {{ color: #6b7280; font-size: 0.85rem; margin-bottom: 2rem; }}
  .flow {{ display: flex; flex-direction: column; align-items: center; gap: 0; }}
  .root {{ background: #1d4ed8; color: #fff; border-radius: 12px;
           padding: 14px 28px; font-weight: 600; font-size: 1rem;
           box-shadow: 0 2px 8px rgba(29,78,216,.25); min-width: 200px; text-align: center; }}
  .root-sub {{ font-size: 0.75rem; font-weight: 400; opacity: 0.8; margin-top: 2px; }}
  .connector {{ width: 2px; height: 36px; background: #cbd5e1; }}
  .branches {{ display: flex; flex-wrap: wrap; gap: 16px; justify-content: center; }}
  .branch {{ display: flex; flex-direction: column; align-items: center; }}
  .key-badge {{ background: #f1f5f9; border: 1.5px solid #cbd5e1; border-radius: 50%;
                width: 32px; height: 32px; display: flex; align-items: center;
                justify-content: center; font-weight: 700; font-size: 0.9rem;
                color: #374151; flex-shrink: 0; }}
  .branch-conn {{ width: 2px; height: 20px; background: #cbd5e1; }}
  .action-box {{ border-radius: 10px; padding: 10px 16px; font-size: 0.82rem;
                 min-width: 140px; text-align: center; border: 1.5px solid transparent; }}
  .action-box .label {{ font-weight: 600; font-size: 0.9rem; margin-bottom: 2px; }}
  .action-box .dest {{ font-size: 0.76rem; opacity: 0.75; margin-top: 4px; word-break: break-all; }}
  .action-box .locked {{ font-size: 0.7rem; opacity: 0.55; margin-top: 2px; }}
  .action-take   {{ background: #f0fdf4; border-color: #86efac; color: #166534; }}
  .action-goto   {{ background: #eff6ff; border-color: #93c5fd; color: #1e40af; }}
  .action-xfer   {{ background: #fffbeb; border-color: #fcd34d; color: #92400e; }}
  .action-hangup {{ background: #fef2f2; border-color: #fca5a5; color: #991b1b; }}
  .action-other  {{ background: #f9fafb; border-color: #e5e7eb; color: #374151; }}
  .ignored {{ margin-top: 1.5rem; font-size: 0.8rem; color: #9ca3af; text-align: center; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 2.5rem;
             padding: 1rem; background: #fff; border-radius: 10px;
             border: 1px solid #e5e7eb; font-size: 0.8rem; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 3px; }}
  .meta {{ margin-top: 2rem; font-size: 0.75rem; color: #9ca3af; }}
  @media (max-width: 600px) {{ body {{ padding: 1rem; }} .branches {{ gap: 10px; }} }}
</style>
</head>
<body>
<h1>Call flow: {handler_name}</h1>
<p class="sub">Source: {host} &nbsp;·&nbsp; {entry_count} menu key(s)</p>

<div class="flow">
  <div class="root">
    {handler_name}
    <div class="root-sub">Call handler</div>
  </div>
  <div class="connector"></div>
  <div class="branches">
    {branches_html}
  </div>
</div>

{ignored_html}

<div class="legend">
  <strong style="align-self:center">Legend:</strong>
  <div class="legend-item"><div class="legend-dot" style="background:#86efac"></div> Take message</div>
  <div class="legend-item"><div class="legend-dot" style="background:#93c5fd"></div> Go to handler / conversation</div>
  <div class="legend-item"><div class="legend-dot" style="background:#fcd34d"></div> Transfer to number</div>
  <div class="legend-item"><div class="legend-dot" style="background:#fca5a5"></div> Hang up</div>
  <div class="legend-item"><div class="legend-dot" style="background:#e5e7eb"></div> Other</div>
</div>

<p class="meta">Generated by cupi_call_flow.py</p>
</body>
</html>
"""

ACTION_CSS = {
    "1": "action-take",
    "2": "action-goto",
    "5": "action-xfer",
    "6": "action-goto",
    "7": "action-hangup",
}


def render_html(handler_name: str, entries: list[dict], host: str) -> str:
    active = [e for e in entries if e.get("Action") not in ("0", None)]
    ignored = [e for e in entries if e.get("Action") == "0"]

    branch_parts = []
    for entry in active:
        key = entry.get("TouchtoneKey", "?")
        action_code = str(entry.get("Action", ""))
        action_label = ACTION_LABELS.get(action_code, f"Action {action_code}")
        css = ACTION_CSS.get(action_code, "action-other")

        dest_parts = []
        if entry.get("TransferNumber"):
            dest_parts.append(f"→ {entry['TransferNumber']}")
        if entry.get("TargetConversation"):
            dest_parts.append(entry["TargetConversation"])
        if entry.get("TargetHandlerObjectId"):
            dest_parts.append(f"OID: {entry['TargetHandlerObjectId'][:20]}")
        dest = "<br>".join(dest_parts)

        locked = "<div class='locked'>🔒 Locked</div>" if entry.get("Locked") == "true" else ""
        dest_html = f"<div class='dest'>{dest}</div>" if dest else ""

        branch_parts.append(f"""
    <div class="branch">
      <div class="key-badge">{key}</div>
      <div class="branch-conn"></div>
      <div class="action-box {css}">
        <div class="label">{action_label}</div>
        {dest_html}
        {locked}
      </div>
    </div>""")

    ignored_html = ""
    if ignored:
        keys = ", ".join(str(e.get("TouchtoneKey", "?")) for e in ignored)
        ignored_html = f'<p class="ignored">Keys with no action: {keys}</p>'

    return HTML_TEMPLATE.format(
        handler_name=handler_name,
        host=host,
        entry_count=len(active),
        branches_html="\n".join(branch_parts) if branch_parts else "<p style='color:#9ca3af'>No active menu keys</p>",
        ignored_html=ignored_html,
    )


# ---------------------------------------------------------------------------
# Interactive handler picker
# ---------------------------------------------------------------------------

def pick_handler(handlers: list[dict]) -> dict:
    print(f"\nFound {len(handlers)} call handler(s):\n")
    for i, h in enumerate(handlers, 1):
        print(f"  {i:>3}.  {h.get('DisplayName', 'Unnamed')}")
    print()
    while True:
        try:
            choice = int(input("Enter number to select a handler: "))
            if 1 <= choice <= len(handlers):
                return handlers[choice - 1]
            print(f"Please enter a number between 1 and {len(handlers)}")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch CUPI call handler menu entries and render a call flow chart."
    )
    parser.add_argument("--host",     required=True, help="CUC hostname or IP")
    parser.add_argument("--user",     required=True, help="CUPI username")
    parser.add_argument("--password", default="",    help="CUPI password (prompted if omitted)")
    parser.add_argument("--handler",  default="",    help="Handler display name to load directly")
    parser.add_argument("--output",   choices=["ascii", "html", "json"], default="ascii",
                        help="Output format (default: ascii)")
    parser.add_argument("--out-file", default="",    help="Write output to this file instead of stdout")
    args = parser.parse_args()

    password = args.password or getpass(f"Password for {args.user}@{args.host}: ")

    print(f"Connecting to https://{args.host} …", file=sys.stderr)
    client = CUPIClient(args.host, args.user, password)

    # Resolve handler
    if args.handler:
        handlers = client.search_handler(args.handler)
        if not handlers:
            print(f"ERROR: No handler found with DisplayName '{args.handler}'", file=sys.stderr)
            sys.exit(1)
        handler = handlers[0]
    else:
        print("Fetching call handlers …", file=sys.stderr)
        handlers = client.get_call_handlers()
        if not handlers:
            print("ERROR: No call handlers returned.", file=sys.stderr)
            sys.exit(1)
        handler = pick_handler(handlers)

    handler_name = handler.get("DisplayName", "Unknown")
    handler_oid  = handler.get("ObjectId", "")

    print(f"Fetching menu entries for '{handler_name}' …", file=sys.stderr)
    entries = client.get_menu_entries(handler_oid)
    print(f"  {len(entries)} menu key(s) found.", file=sys.stderr)

    # Render
    if args.output == "json":
        output = json.dumps(entries, indent=2)
    elif args.output == "html":
        output = render_html(handler_name, entries, args.host)
    else:
        output = render_ascii(handler_name, entries)

    if args.out_file:
        Path(args.out_file).write_text(output, encoding="utf-8")
        print(f"Output written to: {args.out_file}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
