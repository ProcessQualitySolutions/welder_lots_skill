# welder-lots — statistical NDE lot-acceptance skill

*Developed by the [qcdatabase.ai](https://qcdatabase.ai) team.*

An AI-agent skill (Claude Code / Agent SDK) that groups a welder's production
welds into statistical NDE **lots** (radiography / RT lot-acceptance per ASME
B31.1 / B31.3), evaluates each lot as **open** or **closed**, and renders the
deliverable: an XLSX workbook with a cover sheet of open lots plus one tab per
welder. It also performs the uniform random weld draw for examination, with an
audit-log line for every selection.

The system is **read-mostly and deterministic**: the same welds, NDE results,
and filter settings always produce the same lots and the same open/closed
verdicts. The only randomness is the statistical weld draw, which uses the OS
CSPRNG by default and is seedable for reproducible tests.

## What's here

| File | Purpose |
|------|---------|
| `SKILL.md` | Skill manifest + agent-facing instructions (start here). |
| `welder_lots_system_spec.md` | The complete platform-agnostic behavior spec: domain model, lot grouping, open/closed evaluation, qualifying lots, failure escalation, aging warnings, filter presets. |
| `scripts/welder_lots.py` | Pure lot engine — no I/O, no deps (stdlib only); run directly for its built-in self-test. |
| `scripts/build_workbook.py` | Input JSON → XLSX workbook (needs `openpyxl`). |
| `scripts/random_weld_select.py` | Uniform random draw from an open lot's eligible pool + audit log (stdlib only). |
| `scripts/sample_input.json` | A small, runnable example dataset. |
| `scripts/README.md` | Input JSON shape and how an agent drives the scripts. |

## Quick start

```bash
pip install openpyxl                 # only dependency, only for the XLSX
python scripts/build_workbook.py --input scripts/sample_input.json --output welder_lots.xlsx
python scripts/welder_lots.py        # engine self-test
python scripts/random_weld_select.py --log audit.log --welder "John Doe" --lot 3 \
    --nde RT W-2003 W-2004 W-2005
```

Weld/NDE data can come from any source — a quality system (e.g.
[qcdatabase.ai](https://qcdatabase.ai) via its MCP server), a CSV export, or a
database — marshalled into the input JSON documented in
[`scripts/README.md`](scripts/README.md). The scripts never submit NDE
requests themselves; that step stays in your actual quality system.

## Packaging

```bash
python package.py        # -> welder_lots.skill (a zip; load it as a skill)
```

MIT license.

## Repository

[github.com/ProcessQualitySolutions/welder_lots_skill](https://github.com/ProcessQualitySolutions/welder_lots_skill)
