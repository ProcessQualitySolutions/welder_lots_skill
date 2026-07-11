# Welder Lots System — Functional Specification

> **Developed by the QCDatabase.AI team.**
>
> A platform-agnostic specification of the Welder Lots (statistical NDE lot-acceptance)
> system, extracted so it can be re-implemented in any language, database, or UI, or
> packaged as a reusable Claude skill. This document describes **behavior and business
> rules only** — no framework, storage engine, or presentation technology is assumed.

---

## 1. Purpose & Background

In welded piping and pressure-equipment fabrication, codes (e.g. ASME B31.3, B31.1)
allow **statistical / random Non-Destructive Examination (NDE)** rather than examining
every weld. A percentage of each welder's production welds are examined (radiography /
RT being the most common), and welds are grouped into **lots** that are accepted
("closed") or rejected/incomplete ("open") based on pass/fail results.

The Welder Lots System:

1. Collects a welder's eligible welds and groups them chronologically into lots.
2. Evaluates each lot as **closed** (accepted) or **open** (needs more NDE / failing).
3. Enforces an initial **qualifying lot** with stricter rules.
4. Escalates to 100% examination when a lot accumulates too many failures.
5. Lets QC staff **request NDE** on specific welds — either a **random** weld from a lot
   (true statistical sampling) or a **specific** named weld.
6. Tracks weld/package aging against code time limits (e.g. 100 / 160 day warnings).
7. Supports rich, savable **filter presets** so the same lot definition can be reused.

The system is **read-mostly and deterministic**: given the same welds, NDE results, and
filter settings, it always produces the same lots and the same open/closed verdicts.

---

## 2. Domain Model (entities & fields)

Field names below are logical; adapt to any schema. Only fields the lot logic actually
consumes are listed.

### 2.1 Welder (a.k.a. Welder Operator)
| Field | Meaning |
|---|---|
| `id` | Unique welder record id |
| `wid` | Welder ID stamp / mark (the string stamped on welds) |
| `first_name`, `last_name` | Name |
| `comment` | Free-text remarks |
| `start_date` | Optional cutoff. Welds **before** this date are ignored (used when a welder carries over from a prior project where lots may still be open). Sentinel "empty" value = no cutoff. |
| `omit` | Soft-delete flag (omitted welders excluded) |

A weld is attributed to a welder if the welder's `wid` matches **either** the weld's
primary welder mark **or** its *balance* (second) welder mark. See §5.1 (`wid match`).

### 2.2 Weld
| Field | Meaning |
|---|---|
| `id` | Unique weld id |
| `weld_number` | Human-facing weld number/label |
| `date_welded` | Timestamp the weld was made — **drives lot ordering** |
| `welder_id`, `bal_welder_id` | Welder marks (stamps). Either may hold the welder's `wid`. |
| `size`, `sch` | Pipe size and schedule (display only) |
| `joint` | Joint type code (e.g. `BW`, `SW`, `FW` …) — filterable |
| `loc` | Location (`shop`, `field`, or a custom location) — filterable |
| `wps` | Welding Procedure Specification reference — filterable |
| `reweld_code` | Reweld classification. **Repair (`R`) rewelds are excluded** from lots. Included values: `rw`, `c`, empty, or null. |
| `comments` | Free-text weld comment (editable by admins) |
| `omit` | Soft-delete flag |
| → belongs to a **Drawing** |

### 2.3 Drawing
| Field | Meaning |
|---|---|
| `id`, `unit`, `line`, `sheet`, `rev`, `drawing_comment` | Identify/label the drawing |
| `omit` | Soft-delete flag |
| → belongs to a **Package** |

### 2.4 Package
| Field | Meaning |
|---|---|
| `id`, `name` | Identify the package |
| `spec` | Code/spec string (filterable via "spec override") |
| `rt`, `pmi`, `mt`, `pt`, `ht` | **NDE percentage setting per NDE parameter** (integer %). The chosen parameter's value normally selects which welds enter lots. |
| `review_date`, `pre_hydro_date`, `hydro_ok_date`, `sold_date` | Hydro-test lifecycle milestones (see §7.2). Presence/absence flags the package's stage. |
| `omit` | Soft-delete flag |
| `job_ref_id` | Owning job |

### 2.5 Job
| Field | Meaning |
|---|---|
| `id`, `name`, `wo_no`, `po_no` | Identify the job / work order / PO |
| `omit`, `archive` | Omitted jobs excluded from filter lists. **Archived jobs' welds are still included in lots.** |

### 2.6 NDE Report (examination result)
| Field | Meaning |
|---|---|
| `weld_id` | Weld examined |
| `report_id` | Report number |
| `nde_type` | Examination type (`RT`, `MT`, `PT`, `UT`, …) |
| `pass_fail` | Boolean: true = pass, false = fail |
| `date` | Date examined |

A weld may have multiple reports. A weld counts as **passing** if it has ≥1 passing
report (of a matching NDE type) and as **failing** if it has ≥1 failing report.

### 2.7 NDE Request
| Field | Meaning |
|---|---|
| `weld_ref_id` | Weld to be examined |
| `nde_type` | Requested examination type |
| `priority` | `1` (admin only), `2` (default), `3` |
| `location` | Weld location text (required) |
| `requesting_user` | Signature/identity of requester |
| `date_created` | Timestamp |

### 2.8 Random Request (audit record of a random selection)
Captures a random NDE pick: weld, requesting user, welder, parent request, the
exclusion flags used (`exclude_rev/pre/hyd/sold`), the human-readable `filters` string,
the `sample_size` the random draw was made from, and the `lot` number.

### 2.9 Welder Qualification (WPQ) — display support
Per-welder qualification test records (`qual_date`, `inspector`, referenced WPS). Used to
show a welder's certifications; not part of lot math.

### 2.10 Lot Preset
A saved filter set: `name` + serialized filter parameters (`json`) + author signature +
`omit` flag. Lets users re-apply a complete lot definition by name.

---

## 3. Core Concept: How Lots Are Formed

### 3.1 Lot percentage and lot size
- **`lot_perc`** — the examination percentage (e.g. 5, 10, 20). By default it is drawn
  from the package NDE settings present in the data (the smallest sensible value > 1);
  it may be overridden by the user.
- **`lot_size = round(100 / lot_perc)`** — welds per lot. Examples: 5% → 20, 10% → 10,
  20% → 5.

### 3.2 The qualifying lot
- **`quals_required`** (default **2**, user-settable, `0` disables) defines an initial
  **qualifying lot** (labelled **"Q"**, internal lot number `0`).
- The qualifying lot comes **before** all regular lots, requires effectively **100% NDE**,
  and only closes if **all** its welds pass (any failure keeps it open — see §4.1).
- If `quals_required == 0`, there is no qualifying lot and numbering starts at lot **1**.

### 3.3 Failures allowed before 100% (bust limit)
- **`bust_lim`** (default **2**, user-settable) is the number of failed welds a regular
  lot may contain before it must go to 100% examination to close. See §4.2.

### 3.4 Ordering & grouping
1. Collect the welder's **eligible** welds (see §5).
2. Order strictly by `date_welded` ascending, then `id` ascending (stable tiebreak).
3. Walk the ordered list assigning a **lot number** and a **`last_in_lot`** flag:
   - Start lot number at `0` if a qualifying lot is required, else `1`.
   - A weld is the **last in its lot** when, using 1-based position `n = i + 1`:
     ```
     n >= quals_required  AND  (n mod lot_size) == (quals_required mod lot_size)
     ```
     After marking a last-in-lot weld, increment the lot number **only if more welds
     remain** (never leave a trailing empty lot).
   - The **final weld overall is always** marked `last_in_lot` (closes the current lot).

> Intuition: the first `quals_required` welds form lot Q; thereafter every run of
> `lot_size` welds forms lots 1, 2, 3, … The modulo test keeps regular-lot boundaries
> aligned to where the qualifying lot ended.

### 3.5 Which lots are shown
- Lots are displayed **newest first**. By default the **most recent 5 lots** are computed
  and shown; a "load more" action pages further back in history (5 at a time).
- The "top" (most recent) lot can be pinned via an `old_lot` parameter so paging is
  stable while the user scrolls back.

---

## 4. Lot Acceptance Logic (the heart of the system)

For each lot, accumulate over its welds:
- **`points`** = number of welds that **passed** NDE (≥1 passing report).
- **`strikes`** = number of welds that **failed** NDE (≥1 failing report).
- **`current_lot_size`** = number of welds actually in the lot.

Welds with no NDE report yet contribute neither a point nor a strike (they are
"pending"). When the last weld of a lot is reached, evaluate:

### 4.1 Qualifying lot (lot number 0 / "Q")
```
if strikes > 0:                 lot = OPEN     # any failure fails the qualifying lot
elif points >= quals_required:  lot = CLOSED   # all required quals shot and passed
else:                           lot = OPEN      # not enough passing quals yet
```

### 4.2 Regular lot (lot number ≥ 1) — `lot_logic(lot_size, points, strikes)`
Evaluated top-to-bottom; first matching rule wins:
```
if points >= 1 and strikes < 1:                 return CLOSED
    # at least one pass and no failures → accepted

elif strikes <= bust_lim and points/strikes >= 2: return CLOSED
    # within the failure budget AND at least 2 passing welds per failed weld

elif strikes > bust_lim and (points + strikes) < lot_size: return OPEN
    # too many failures → 100% NDE required, and not every weld examined yet

elif (points + strikes) >= lot_size:            return CLOSED
    # every weld in the lot has been examined (lot "shot out") → resolved/closed

else:                                            return OPEN
```

**Plain-language summary of a regular lot:**
- A lot **closes** as soon as it has a passing weld and no failures.
- If there are failures but they are **within the bust limit** and there are at least
  **two passing welds for every failed weld**, the lot still closes.
- If failures **exceed the bust limit**, the lot requires **100% examination**; it stays
  **open** until every weld in the lot has an NDE result, at which point it closes
  (its acceptance/rejection having been recorded weld-by-weld).

### 4.3 Open vs. closed outputs
- Each evaluated lot yields: a **status** (`closed`/`open`), a **label** (`Q` or the lot
  number), the **weld count**, and the **pass/fail tallies**.
- An **open-lot counter** is incremented for every open lot (a welder-level "how many lots
  still need attention" figure).
- A lot that is the *current* (most recent, not-yet-full) lot is also evaluated with the
  same `lot_logic`, so an in-progress lot shows its provisional status.

---

## 5. Weld Eligibility & Filtering

### 5.1 Base eligibility (always applied)
A weld enters the candidate set only if **all** hold:
- Weld, its drawing, and its package are **not omitted**.
- `date_welded` is **after** the welder's `start_date` cutoff.
- The weld is attributed to the welder — the welder's `wid` matches the primary **or**
  balance welder mark. Implement as a whitespace-delimited containment test so partial
  numeric marks don't false-match, e.g. treat the marks as `". <primary> <balance> ."`
  and test for `" <wid> "` inside it (the surrounding spaces/sentinels are essential).
- `reweld_code` ∈ {`rw`, `c`, empty, null} — i.e. **Repair (`R`) rewelds are excluded.**
- **Package NDE-percentage match** (default mode): the chosen package parameter equals
  `lot_perc` (see §5.3). *This condition is replaced when spec-override or job-override
  mode is active — see §5.4.*

Archived jobs are **included**. Omitted drawings/packages are **excluded**.

### 5.2 Optional filters (post-query, AND-combined)
Each of the following, when supplied, further restricts the candidate welds. Defaults are
chosen so an unfiltered run yields the conventional "RT, butt-welds, all locations" lot:

| Filter | Behavior | Default |
|---|---|---|
| **NDE type** | Restrict which NDE report types count toward pass/fail for the lot. | `RT` only |
| **Joint type** | Keep only welds whose joint code is selected. | `BW` (butt weld) only |
| **Location** | Keep only welds at selected location(s). If custom locations exist they appear as multi-select; otherwise a Shop/Field choice. | All locations |
| **WPS** | Keep only welds using selected welding procedure(s). | All WPS |
| **Spec override** | See §5.4. | off |
| **Job override** | See §5.4. | off |

Any deviation from the defaults marks the run as having an **active (non-default) filter**
(surfaced in the UI and in exports).

### 5.3 Package NDE parameter
`pkg_param` selects **which** package percentage column drives §5.1's percentage match:
`rt` (volumetric radiography — **default & recommended**), `ht` (hardness, e.g. Brinell),
`pmi`, `mt`, `pt`. Choosing anything but `rt` counts as a non-default filter.

### 5.4 Override modes (mutually exclusive with the % match)
When **spec override** and/or **job override** is active, the package-percentage match in
§5.1 is **dropped**, and welds are instead selected by:
- **Spec override (`spco_*`)** — include welds whose package `spec` is among the selected
  specs, **regardless** of that package's NDE % setting. Intended to pull welds by spec
  rather than by each package's configured NDE percentage. In this mode the user supplies
  an explicit **lot percentage override** (typically 5–50%) to set `lot_size`.
- **Job override (`jid_*`)** — include welds only from the selected (non-archived) jobs.

These modes are for targeted sampling and **cannot be combined with** the normal
percentage-based selection as an *additional* filter — they *replace* it.

### 5.5 The filter-explanation string
Every active setting appends a token to a human-readable **explain string**, e.g.:
```
PERCENTAGE:5%  NO_OF_QUALIFIERS:2  FAIL_LIMIT:2  NDE_PKG_PARAMETER:RT
NDE:RT  JOINT:BW  LOCATION:SHOP  WPS:PROC_123  SPEC_OVERRIDE:A106_B  JOB_OVERRIDE:UNIT_40
```
This string is shown beside the lots, embedded in exports/PDF, and stored with saved
presets. It is the canonical, portable description of a lot definition.

---

## 6. NDE Requesting Workflow

From an open lot the user can request examinations. Two modes:

### 6.1 Random NDE request (true statistical sampling)
1. The user opens the request form for a specific **lot**.
2. Build the **selection pool** = welds in that lot **excluding**:
   - welds that **already have a passing NDE** (a passing weld is never re-requested), and
   - optionally, welds whose package is at a given hydro-lifecycle stage (see §7.2):
     reviewed, pre-hydro approved, hydro tested, and/or reinstated — each independently
     toggleable. (Defaults exclude pre-hydro / hydro / reinstated, include reviewed.)
3. If fewer than **2** welds remain in the pool, refuse the random request (not enough
   welds to sample).
4. Pick a **uniformly random** weld from the pool (`floor(random() * pool_size)`), record
   the `sample_size`, and create an NDE request for it.
5. Persist both an **NDE request** and a **random-request audit record** (§2.8) capturing
   the exclusion flags, filter string, sample size, and lot.

> The randomization is intentionally simple and uniform. Any re-implementation must
> preserve **uniform selection over the eligible pool** and the **pool ≥ 2** guard.

**Bundled implementation.** A lightweight, dependency-free reference implementation of the
draw + audit log ships with the skill at `scripts/random_weld_select.py`. It takes a log
path and a pool of weld ids/labels, uniformly selects one (OS CSPRNG by default; seedable
for tests), enforces the pool ≥ 2 rule, and appends an audit line such as:
```
2026-07-11T14:03:22Z  W-1042 selected for RT from [W-1001, W-1042, W-1099] for John Doe lot 3 (sample size 3)
```
It performs **selection and logging only** — it does not contact any quality/NDE system.
After the draw, the agent or user still submits the actual NDE request in whatever quality
system is in use (see §6.3). This keeps the random draw auditable and reproducible
independent of the storage/UI layer.

### 6.2 Non-random (specific) NDE request
The user names a specific weld and requests an NDE type for it directly (no sampling, no
pool). Used for follow-ups, repairs, or ad-hoc examinations.

### 6.3 Common request fields & rules
- **Location** (required text), **NDE type** (from the allowed NDE type list), **priority**
  (`1` requires admin; otherwise `2` default / `3`).
- On success, the weld's row reflects the new active request (who requested it, type, and
  whether it originated from a random draw). A request is considered **fulfilled** once a
  matching NDE report dated after the request exists.

---

## 7. Supporting Features

### 7.1 Days-elapsed / aging warnings
For each welder, compute the age (in days) of their **oldest still-relevant weld** and
surface code-driven warnings:
- **≥ 100 days** → caution warning.
- **≥ 160 days** → escalated (danger) warning.

This tracks the code requirement to complete NDE within a time window after welding.

### 7.2 Hydro-test lifecycle package stages
A package advances through milestones, each flagged by the presence of a date:
| Milestone date present | Stage | Used to exclude from random sampling |
|---|---|---|
| `review_date` | **Reviewed** | optional |
| `pre_hydro_date` | **Pre-hydro approved** | optional (default exclude) |
| `hydro_ok_date` | **Hydro tested** | optional (default exclude) |
| `sold_date` | **Reinstated** | optional (default exclude) |
Once a package is hydro-tested, requesting more random NDE on its welds is usually
undesirable — hence the exclusion toggles in §6.1.

### 7.3 Welder list & pagination
- A paginated, searchable list of welders (search by `wid`, first, or last name).
- For each welder the list shows a compact **lot icon summary** (last 5 lots as
  closed/open icons), a **days-elapsed** figure, and their **qualification certs**.
- Page size and pagination are parameters; total counts drive the page controls.

### 7.4 Editing (admin-gated)
- **Weld date/time** edits (year/month/day/hour/min/sec) — admin only. Because
  `date_welded` drives lot ordering, editing it re-shapes lots.
- **Weld comment** edits — admin only; non-admins see a read-only comment indicator.

### 7.5 Exports
The expanded lot view can be exported to **PDF**, **XLSX**, and **CSV**. Each weld row
carries a full set of machine-readable attributes (lot number, weld number, size, sch,
joint, date welded, unit/line/sheet/rev, drawing comment, package, spec, and the weld's
NDE type/report/date/pass-fail lists) so exports are self-describing. The active filter
explain string (§5.5) is included as a header/preamble.

The **XLSX workbook is the skill's primary, batteries-included deliverable** (see §12).

### 7.6 Filter presets (save / apply / retire)
- **Save**: persist the current filter set under a sanitized, unique name (names are
  normalized — restricted charset, single spaces collapsed; duplicate active names
  rejected). Stored as name + serialized filters + author.
- **Apply**: selecting a preset repopulates every filter control to reproduce that lot
  definition exactly.
- **Retire**: presets are soft-deleted (`omit`) to free their name.

---

## 8. Expanded Lot View — per-weld row contents

When a lot is expanded, each weld shows: weld number (link to its drawing/map), size &
schedule, joint, **date welded**, drawing identity (unit/line/sheet/rev + comment),
package (+ spec) with **hydro-stage indicators**, all **NDE results** (type-report,
pass/fail, date), any **active NDE requests** (requester, type, fulfilled?), and whether a
request came from a **random** draw. A per-lot banner shows the lot label, closed/open
status, weld count, and pass/fail tallies, plus a one-click **"random request"** action.

---

## 9. Reference Algorithm (consolidated pseudocode)

```text
function build_welder_lots(welder, settings):
    perc      = settings.lot_perc
    lot_size  = round(100 / perc)
    qreq      = settings.quals_required        # default 2, 0 disables qual lot
    bust_lim  = settings.bust_lim              # default 2

    # 1. Gather eligible welds (see §5)
    welds = eligible_welds(welder, settings)             # base + optional filters
    welds = sort(welds, by=[date_welded ASC, id ASC])
    if welds is empty: return "No lots found"

    # 2. Assign lot numbers + last_in_lot flags (see §3.4)
    lot_no = (qreq > 0) ? 0 : 1
    for i, w in enumerate(welds):            # i is 0-based
        w.lot = lot_no
        n = i + 1                            # 1-based position
        if n >= qreq and (n mod lot_size) == (qreq mod lot_size):
            w.last_in_lot = true
            if exists welds[i+1]: lot_no += 1
    welds[last].last_in_lot = true

    # 3. Evaluate each shown lot (see §4)
    points = strikes = size = 0
    results = []
    for w in welds (limited to the shown lot window):
        p_pass = any passing NDE report on w matching settings.nde_types
        p_fail = any failing NDE report on w matching settings.nde_types
        if p_pass: points += 1
        if p_fail: strikes += 1
        size += 1
        if w.last_in_lot:
            if w.lot == 0:                                   # qualifying lot
                closed = (strikes == 0 and points >= qreq)
            else:                                            # regular lot
                closed = lot_logic(size, points, strikes, bust_lim)
            results.append({lot: w.lot==0 ? "Q" : w.lot,
                            status: closed ? "closed" : "open",
                            welds: size, pass: points, fail: strikes})
            points = strikes = size = 0
    return results

function lot_logic(lot_size, points, strikes, bust_lim):
    if points >= 1 and strikes < 1:                       return CLOSED
    if strikes <= bust_lim and points/strikes >= 2:       return CLOSED
    if strikes >  bust_lim and (points+strikes) < lot_size: return OPEN
    if (points + strikes) >= lot_size:                    return CLOSED
    return OPEN
```

---

## 10. Invariants & Edge Cases (must-preserve behavior)

1. **Repair (`R`) rewelds are never counted** in lots.
2. **Ordering is strictly chronological** (`date_welded`, then `id`); editing a weld's
   date re-shapes lots. Ties broken by id for determinism.
3. **Qualifying lot fails on any single failure**, regardless of bust limit.
4. **No trailing empty lot**: the lot counter only advances when another weld follows.
5. **The final weld always closes its lot**, even if the lot is under `lot_size`.
6. **Pending welds** (no NDE result) count toward `current_lot_size` but add neither a
   point nor a strike; they keep a not-yet-full lot open unless the pass/no-fail rule
   already closed it.
7. **Welder `start_date`** hard-excludes earlier welds (prior-project carryover).
8. **`wid` matching must be whitespace-delimited** across both primary and balance marks
   to avoid substring false matches (e.g. `wid=1` must not match `12`).
9. **Random pool must have ≥ 2 welds**; passing welds are always excluded from the pool.
10. **Override modes replace, never augment**, the package-percentage selection.
11. **Default lot** = 5%-style RT, butt-weld, all-locations, 2 quals, 2 bust limit; any
    deviation flags a "non-default filter" state.
12. **Priority 1 NDE requests require admin**; weld date/comment edits require admin.

---

## 11. Portability Notes (for re-implementation / skill packaging)

- **Storage**: any store that can hold the §2 entities and answer "give me this welder's
  non-omitted, non-Repair welds after `start_date`, joined to drawing/package/job, ordered
  by date." No stored procedures or DB-specific features are required.
- **Determinism**: all lot math is pure given the input set — trivially unit-testable.
  The only nondeterminism is the **random NDE draw** (§6.1), which should stay uniform.
- **UI-agnostic**: this spec prescribes data and rules, not widgets. Any front end
  (web, TUI, API, chat/skill) can drive it via: *(a)* a filter/settings object, *(b)* a
  welder id, returning the lot results of §4.3 and the expanded rows of §8.
- **Suggested API surface** for a skill/library:
  - `list_welders(query, page)` → paginated welders with lot-icon summaries.
  - `get_welder_lots(welder_id, settings)` → lot results + expanded weld rows.
  - `request_nde(weld_id, nde_type, location, priority)` → specific request.
  - `request_random_nde(lot, settings, exclusions)` → uniform random pick + audit record.
    The pick + audit log is provided ready-to-use by `scripts/random_weld_select.py`
    (§6.1); the caller supplies the already-filtered pool and still submits the resulting
    NDE request to the quality system separately.
  - `save_preset(name, settings)` / `apply_preset(id)` / `retire_preset(id)`.
  - `welder_days_elapsed(welder_id)` → aging figure with 100/160-day thresholds.
- **Glossary**: NDE = Non-Destructive Examination; RT = Radiographic Testing; MT/PT =
  Magnetic-particle / Dye-penetrant; HT = Hardness; PMI = Positive Material ID;
  WPS = Welding Procedure Specification; WPQ = Welder Performance Qualification;
  WID = Welder ID stamp; "bust limit" = failures allowed before 100% examination.

---

## 12. Primary Deliverable: the XLSX Workbook

The skill's default, "good enough for most requirements" output is a single **XLSX
workbook** generated from the computed lots. It is deliberately dry and portable — no
server, no live app — so it can be emailed, archived, or opened by any inspector.

**Structure:**
- **Cover sheet — "Open Lots" (attention list).** Every lot whose status is *open*,
  across all welders, most-urgent first (longest-waiting, then most failures). Columns:
  *Welder, WID, Lot, Welds, Pass, Fail, Oldest weld, Days waiting.* A header band
  summarizes the run (project, timestamp, and the active settings/filter string of §5.5)
  and the total welder / open-lot counts. When nothing is open, it states that plainly.
- **One tab per welder** (tab named by WID). The welder's **full weld history** in
  chronological order, with **every row tagged with its lot label and lot status**.
  Columns: *Lot, Status, Weld #, Date welded, Joint, Size, Sch, Loc, WPS, Drawing,
  Package, Spec, NDE type, Report, NDE date, Result.* Qualifying-lot rows are tinted
  amber, open lots red, closed lots green; failed NDE results are bold red; each weld's
  Result is `PASS` / `FAIL` / `PENDING`.

**Reference implementation** ships with the skill under `scripts/` (see
`scripts/README.md`):
- `welder_lots.py` — the pure lot engine (this document's §3, §4, §9), no dependencies.
- `build_workbook.py` — reads the input JSON (§11 shape) and writes the workbook
  (depends only on `openpyxl`).
- `random_weld_select.py` — the random draw + audit log of §6.1.

The workbook is only a default. Because all logic lives in the engine module, any other
presentation — a live dashboard, a PDF, an HTML artifact, a chat summary — can be built
on the same numbers without re-deriving the lot math.

---

*Specification developed by the QCDatabase.AI team. Released as open source for use in
building compatible tools and Claude skills.*
