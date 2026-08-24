---
name: welder-lots
description: >-
  Group a welder's production welds into statistical NDE lots (radiography /
  RT lot-acceptance per ASME B31.1 / B31.3), evaluate each lot as open or
  closed, and render an XLSX workbook (cover sheet of open lots plus one tab
  per welder). Use when the user works with welder lots, statistical/random
  NDE, weld lot-acceptance, qualifying lots, or needs a random weld drawn from
  an open lot for examination. Pure-Python lot engine with no required
  dependencies; only the XLSX output needs openpyxl.
license: MIT
---

# Welder Lots — statistical NDE lot-acceptance

*Developed by the [qcdatabase.ai](https://qcdatabase.ai) team.*

In welded piping and pressure-equipment fabrication, codes (ASME B31.1,
B31.3) allow **statistical / random NDE** instead of examining every weld: a
percentage of each welder's production welds is examined (RT being the most
common), and welds are grouped into **lots** that are accepted ("closed") or
left "open" based on pass/fail results. This skill collects a welder's
eligible welds, groups them chronologically into lots, evaluates each lot,
enforces the stricter initial **qualifying lot**, escalates to 100%
examination after too many failures, and produces the deliverable workbook.

The system is **read-mostly and deterministic**: the same welds, NDE results,
and filter settings always produce the same lots and the same open/closed
verdicts. The only randomness is the statistical weld draw, which uses the OS
CSPRNG by default and is seedable for reproducible tests.

## Learn the rules first

Read the full behavior spec before implementing or debugging lot logic — it is
a plain bundled file, open it directly:

| File | Covers |
|------|--------|
| `welder_lots_system_spec.md` | The complete platform-agnostic spec: domain model, lot grouping, open/closed evaluation, qualifying lots, failure escalation, aging warnings, and filter presets. **Start here.** |
| `scripts/README.md` | The reference scripts, input JSON shape, and how an agent is expected to drive them. |

## Scripts

| File | What it does | Dependencies |
|------|--------------|--------------|
| `scripts/welder_lots.py` | Pure lot engine: groups welds into lots and evaluates open/closed per the spec. No I/O, no deps — importable and unit-testable. Run directly for its built-in self-test. | stdlib only |
| `scripts/build_workbook.py` | Reads input JSON, computes lots via the engine, writes the XLSX (cover sheet + per-welder tabs). | `openpyxl` |
| `scripts/random_weld_select.py` | Uniformly draws one weld from an open lot's eligible pool and appends an audit-log line. Selection + logging only — never contacts a quality system. | stdlib only |
| `scripts/sample_input.json` | A small, runnable example dataset. | — |

## Typical flow

1. Gather weld/NDE data (from a quality system / MCP, a CSV export, or a
   database) and marshal it into the input JSON documented in
   `scripts/README.md`, applying any joint/location/WPS/percentage filtering.
2. Run `build_workbook.py` to produce the XLSX deliverable.
3. When an open lot needs a random examination, run `random_weld_select.py`
   on that lot's eligible weld pool, then submit the resulting NDE request in
   the actual quality system (the scripts never do that step for you).

```bash
python scripts/build_workbook.py --input scripts/sample_input.json --output welder_lots.xlsx
python scripts/welder_lots.py            # engine self-test
```
