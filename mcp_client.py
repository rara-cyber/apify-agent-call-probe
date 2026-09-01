"""Minimal Apify MCP client - enough to see what an LLM sees.

No SDK. Streamable HTTP + SSE parsing in ~60 lines, so you can paste it anywhere.
Reads APIFY_TOKEN from the environment.
"""
import json, os, urllib.request, urllib.error

BASE = "https://mcp.apify.com"


def _token():
    """APIFY_TOKEN from the environment, falling back to the Apify CLI's local store."""
    tok = os.environ.get("APIFY_TOKEN")
    if tok:
        return tok
    for path in ("~/.apify/auth.json", "~/.apify/secrets.json"):
        try:
            data = json.load(open(os.path.expanduser(path)))
            if data.get("token") or data.get("APIFY_TOKEN"):
                return data.get("token") or data["APIFY_TOKEN"]
        except (OSError, ValueError):
            continue
    raise RuntimeError("Set APIFY_TOKEN, or run `apify login` first.")


def _parse_sse(body):
    """Apify answers JSON-RPC over SSE. Pull the last `data:` payload."""
    out = None
    for line in body.splitlines():
        if line.startswith("data:"):
            out = json.loads(line[5:].strip())
    return out


class Mcp:
    def __init__(self, tools=None, timeout=180):
        self.url = BASE + (f"?tools={tools}" if tools else "")
        self.sid = None
        self.timeout = timeout
        self._id = 0
        self._init()

    def _post(self, payload, notify=False):
        h = {
            "Authorization": "Bearer " + _token(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=h, method="POST"
        )
        try:
            r = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:400].decode()}") from None
        if not self.sid:
            self.sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        body = r.read().decode()
        return None if notify else _parse_sse(body)

    def _init(self):
        self._post({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "apify-agent-call-probe", "version": "0.1.0"},
            },
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notify=True)

    def rpc(self, method, params=None):
        self._id += 1
        res = self._post({"jsonrpc": "2.0", "id": self._id, "method": method,
                          "params": params or {}})
        if res and "error" in res:
            raise RuntimeError(f"{method} -> {res['error']}")
        return (res or {}).get("result", {})

    def list_tools(self):
        return self.rpc("tools/list").get("tools", [])

    def call(self, name, args):
        return self.rpc("tools/call", {"name": name, "arguments": args})


def text_of(result):
    """Flatten an MCP tool result's content blocks to plain text."""
    return "\n".join(
        c.get("text", "") for c in (result or {}).get("content", []) if c.get("type") == "text"
    )
