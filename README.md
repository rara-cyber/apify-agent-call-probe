# apify-agent-call-probe

Call an Apify Actor the way an LLM would, and find out whether an agent could tell the call failed.

When an AI client reaches your Actor through the [Apify MCP server](https://mcp.apify.com), it does not
get the Console's rendered form. It gets your `INPUT_SCHEMA.json` as a JSON Schema and sends exactly
what that schema declares. If a load-bearing field carries a `prefill` (a Console hint) but no
`default` (what the platform actually injects), the tool advertises `required: []` and the model is
told it may call with no arguments at all.

Plenty of Actors then "succeed" with nothing, and no signal a model reads says otherwise.

## Install

No dependencies. Python 3.9+.

```bash
git clone https://github.com/rara-cyber/apify-agent-call-probe
cd apify-agent-call-probe
export APIFY_TOKEN=your_token_here
```

## Use

```bash
python3 agent_call_probe.py <username>/<actor-name>
```

```text
$ python3 agent_call_probe.py sian.agency/apartments-com-property-scraper

SILENT-STATS  status=SUCCEEDED isError=False items=1
Actor knew what was wrong but never surfaced it:
   failures[0].error: location must be a non-empty string.
```

Output captured in July 2026. That Actor has since been fixed, so the same call returns `OK`
today: `location` now carries a `default` rather than only a `prefill`. That is the change this
probe exists to prompt.

Run the built-in checks with `python3 agent_call_probe.py --self-check`.

## Verdicts

Best to worst:

| verdict | meaning |
|---|---|
| `REJECTED` | The MCP server refused the call with `-32602`. The model gets an actionable protocol error and no run is billed. |
| `OK` | Rows came back. |
| `LOUD-FAIL` | The run failed visibly. Not free, but the agent can see it. |
| `SILENT-EMPTY` | `SUCCEEDED` with zero rows. Indistinguishable from "no results exist". |
| `SILENT-STATS` | `SUCCEEDED` with rows that are run-summary objects, not data. The model will likely read them as results. |

`SILENT-EMPTY` and `SILENT-STATS` are the ones to care about. An agent calling that Actor today is
reporting your failure to its user as a fact about the world.

The probe also recovers **buried errors**: error strings the Actor recorded somewhere in its output
but never raised. These are common, and they are usually correct, which is what makes them
frustrating: the Actor diagnosed the problem and filed it where nothing will look.

## Cost

Each probe is one real Actor run, typically 0.0001 to 0.005 compute units, because the failing paths
exit early. Sweeping a whole account costs roughly nothing. Actors that succeed on an empty input will
do real work, so check anything expensive before pointing the probe at it.

## Fixing what it finds

Give every load-bearing field a `default`, not just a `prefill`:

```json
{ "location": { "type": "string", "prefill": "Austin, TX", "default": "Austin, TX" } }
```

Never return silence. If you cannot produce results, emit a typed row saying so, and keep your dataset
schema fields `nullable: true` so it survives validation:

```js
await Actor.pushData({ status: 'no_results', reason: 'location must be a non-empty string' });
```

And do not gate inference on a field the platform always injects. Apify writes schema defaults into
every run's input, so `if (!input.mode)` never fires on the platform.

## Files

- `mcp_client.py` — a ~60-line dependency-free Apify MCP client (Streamable HTTP + SSE)
- `agent_call_probe.py` — the probe, with an assert-based self-check

## Licence

MIT
