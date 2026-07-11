# Welder Lots System — scripts

*Developed by the QCDatabase.AI team. Open source.*

Reference implementation that turns welder/weld/NDE data into welder **lots**
(statistical NDE lot-acceptance) and renders the primary deliverable: an **XLSX
workbook** with a cover sheet of open lots plus one tab per welder. See
[`../welder_lots_system_spec.md`](../welder_lots_system_spec.md) for the full
platform-agnostic behavior these scripts implement.

## Files

| File | What it does | Dependencies |
|---|---|---|
| `welder_lots.py` | Pure lot engine: groups welds into lots and evaluates open/closed per the spec (§3, §4, §9). No I/O, no deps — importable and unit-testable. | stdlib only |
| `build_workbook.py` | Reads input JSON, computes lots via the engine, writes the XLSX (cover sheet + per-welder tabs). | `openpyxl` |
| `random_weld_select.py` | Uniformly selects one weld from a pool and appends an audit-log line. Selection + logging **only** — never contacts a quality system. | stdlib only |
| `sample_input.json` | A small, runnable example dataset. | — |

## Quick start

```bash
# 1. Build the workbook (main output)
python build_workbook.py --input sample_input.json --output welder_lots.xlsx

# 2. Sanity-check the engine
python welder_lots.py            # runs its built-in self-test

# 3. Random NDE draw + audit log (when sampling an open lot)
python random_weld_select.py --log audit.log --welder "John Doe" --lot 3 \
    --nde RT W-2003 W-2004 W-2005
```

Install the one dependency if needed: `pip install openpyxl`.

## Input JSON shape

```jsonc
{
  "project": "Unit 40 Piping",                 // optional, cover label
  "generated_by": "QCDatabase.AI",             // optional, cover label
  "settings": {                                // all optional; spec defaults shown
    "lot_perc": 5, "quals_required": 2, "bust_lim": 2, "nde_types": ["RT"]
  },
  "welders": [
    {
      "wid": "12", "name": "John Doe", "start_date": null,
      "welds": [
        {
          "weld_number": "W-1001", "date_welded": "2026-01-05",
          "reweld_code": "",                   // "R" => Repair reweld, excluded
          "joint": "BW", "loc": "shop", "wps": "P1", "size": "6", "sch": "40",
          "drawing": "U40-L12-S1", "package": "PKG-A", "spec": "A106",
          "nde": [{"type": "RT", "pass": true, "report": "R-500", "date": "2026-01-10"}]
        }
      ]
    }
  ]
}
```

A flat top-level `"welds"` array is also accepted; welds are grouped onto welders
by a `welder_wid` / `welder` / `wid` key.

### How the agent is expected to use these

1. Gather weld/NDE data from wherever it lives (a quality system / MCP, a CSV
   export, a database) and marshal it into the input JSON above. Apply any
   joint/location/WPS/percentage filtering (spec §5) while marshaling, or supply
   the fields and let the engine handle the core rules.
2. Run `build_workbook.py` to produce the XLSX deliverable.
3. When an open lot needs a random examination, run `random_weld_select.py` on
   that lot's eligible weld pool, then submit the resulting NDE request in the
   actual quality system (the scripts never do that step for you).

## Workbook layout

- **`Open Lots`** (cover): every open lot across all welders, most-urgent first
  (longest-waiting, then most failures), with pass/fail tallies, the oldest weld
  date, and days waiting. Says so plainly when everything is closed.
- **One tab per welder** (named by WID): the welder's full weld history in
  chronological order, every row tagged with its **lot** and **lot status**;
  qualifying-lot rows amber, open lots red-tinted, closed lots green-tinted,
  failed NDE results in bold red.

## Notes

- The engine is deterministic; the only randomness is the weld draw, which uses
  the OS CSPRNG by default and is seedable (`--seed`) for reproducible tests.
- Everything here is UI-agnostic. The XLSX is the batteries-included default;
  build artifacts, dashboards, or other tooling on top of `welder_lots.py` as
  needed.
