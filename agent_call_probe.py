#!/usr/bin/env python3
"""agent_call_probe — call an Apify Actor the way an LLM would, and report whether
an agent could actually tell the call failed.

    python3 agent_call_probe.py sian.agency/home-depot-product-scraper
    python3 agent_call_probe.py --self-check

Why: the Apify MCP server builds an Actor's tool schema from INPUT_SCHEMA.json. If a
load-bearing field carries `prefill` (a Console hint) but no `default` (what the platform
actually injects), the tool advertises `required: []` — so a model is told it may call
with no arguments. Many Actors then "succeed" with nothing, and no signal an LLM reads
says otherwise.

Verdicts, best to worst:
  REJECTED       MCP refused the call (-32602) because the Actor declares `required`.
                 The model gets an actionable protocol error and never burns a run.
  OK             rows came back
  LOUD-FAIL      the run failed visibly — not free, but the agent can see it
  SILENT-EMPTY   SUCCEEDED with 0 rows — indistinguishable from "no results exist"
  SILENT-STATS   SUCCEEDED with rows, but they are run-summary rows, not data.
                 Worst case: the model will read them as results.
"""
import json, sys
from mcp_client import Mcp, text_of

# Keys that mean "this row describes the run", not "this row is a result".
STATS_MARKERS = {
    "__retryHelper", "queriesRequested", "queriesSucceeded", "queriesFailed",
    "listingsPushed", "duplicatesSkipped", "kpis", "failures", "totalResultsAvailable",
}


def find_buried_errors(row, path="", out=None):
    """Recover error text the Actor recorded but never raised."""
    out = [] if out is None else out
    if isinstance(row, dict):
        for k, v in row.items():
            p = f"{path}.{k}" if path else k
            if k in ("error", "errorMessage") and isinstance(v, str) and v.strip():
                out.append((p, v))
            else:
                find_buried_errors(v, p, out)
    elif isinstance(row, list):
        for i, v in enumerate(row):
            find_buried_errors(v, f"{path}[{i}]", out)
    return out


def classify(status, items, is_error):
    if is_error or status not in ("SUCCEEDED", None):
        return "LOUD-FAIL"
    if not items:
        return "SILENT-EMPTY"
    if all(STATS_MARKERS & set(r) for r in items if isinstance(r, dict)):
        return "SILENT-STATS"
    return "OK"


def probe(actor, timeout=600):
    m = Mcp(tools=actor, timeout=timeout)
    tool = next(t["name"] for t in m.list_tools()
                if t["name"].startswith(actor.split("/")[0].replace(".", "-dot-") ))
    schema = next(t for t in m.list_tools() if t["name"] == tool).get("inputSchema", {})
    props = schema.get("properties", {}) or {}
    no_default = [k for k, v in props.items() if "default" not in v]

    try:
        res = m.call(tool, {})                  # <- exactly what a model sends
    except RuntimeError as e:
        # -32602 = the MCP server validated `required` and refused. Best outcome:
        # the model is corrected before an Actor run is ever billed.
        if "-32602" in str(e):
            return {"actor": actor, "tool": tool, "verdict": "REJECTED",
                    "status": None, "isError": True, "itemCount": 0,
                    "declared_required": schema.get("required", []),
                    "fields_without_default": no_default,
                    "buried_errors": [], "rpc_error": str(e)[:300]}
        raise
    meta = json.loads(res["content"][0]["text"])
    status = meta.get("status")
    ds = ((meta.get("storages") or {}).get("datasets") or {}).get("default") or {}

    items = []
    if ds.get("id") and ds.get("itemCount"):
        got = json.loads(text_of(m.call("get-dataset-items",
                                        {"datasetId": ds["id"], "limit": 20})).split("\n")[0])
        items = got.get("items", []) if isinstance(got, dict) else got

    verdict = classify(status, items, res.get("isError"))
    buried = [e for r in items for e in find_buried_errors(r)]
    return {
        "actor": actor, "tool": tool, "verdict": verdict, "status": status,
        "isError": bool(res.get("isError")), "itemCount": ds.get("itemCount", 0),
        "declared_required": schema.get("required", []),
        "fields_without_default": no_default,
        "buried_errors": buried,
    }


def _self_check():
    assert classify("SUCCEEDED", [], False) == "SILENT-EMPTY"
    assert classify("SUCCEEDED", [{"price": 9, "title": "x"}], False) == "OK"
    assert classify("SUCCEEDED", [{"__retryHelper": True, "failures": [],
                                   "queriesRequested": 0, "queriesSucceeded": 0,
                                   "queriesFailed": 0, "listingsPushed": 0,
                                   "duplicatesSkipped": 0, "kpis": None,
                                   "totalResultsAvailable": 0}], False) == "SILENT-STATS"
    assert classify("FAILED", [], False) == "LOUD-FAIL"
    assert classify("SUCCEEDED", [], True) == "LOUD-FAIL"
    row = {"failures": [{"query": "(input)", "error": "location must be a non-empty string."}]}
    assert find_buried_errors(row) == [
        ("failures[0].error", "location must be a non-empty string.")]
    assert find_buried_errors({"a": {"b": {"errorMessage": "deep"}}}) == [("a.b.errorMessage", "deep")]
    assert find_buried_errors({"error": ""}) == []          # empty string is not an error
    # a declared `required` is the only thing that earns a REJECTED verdict
    assert classify("SUCCEEDED", [{"a": 1}], False) != "REJECTED"
    print("self-check OK")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check(); sys.exit(0)
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    r = probe(sys.argv[1])
    print(json.dumps(r, indent=1))
    print(f"\n{r['verdict']}  status={r['status']} isError={r['isError']} items={r['itemCount']}")
    if r["buried_errors"]:
        print("Actor knew what was wrong but never surfaced it:")
        for p, e in r["buried_errors"]:
            print(f"   {p}: {e}")
