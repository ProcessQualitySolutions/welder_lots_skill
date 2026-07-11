#!/usr/bin/env python3
"""
welder_lots.py — pure, dependency-free implementation of the Welder Lots engine.

Developed by the QCDatabase.AI team. Released as open source as part of the
Welder Lots System skill.

This module implements the lot-formation and lot-acceptance logic described in
``welder_lots_system_spec.md`` (see §3, §4, §9). It has NO third-party
dependencies and NO I/O — it turns a list of welds into lots with open/closed
verdicts, so it can be unit-tested in isolation and reused by any front end
(the XLSX builder, an API, a chat skill, etc.).

Input weld shape (a plain dict; extra keys are preserved untouched):

    {
        "weld_number": "W-1001",       # required, human label
        "date_welded": "2026-01-05",   # required, ISO date or datetime string
        "reweld_code": "",             # "r"/"R" => excluded (Repair reweld)
        # ---- everything below is optional / passthrough ----
        "joint": "BW", "loc": "shop", "wps": "P1",
        "size": "6", "sch": "40",
        "drawing": "U40-L12-S1", "package": "PKG-A", "spec": "A106",
        "nde": [
            {"type": "RT", "pass": true, "report": "R-500", "date": "2026-01-10"}
        ]
    }

Settings shape (all optional; defaults match the spec):

    {"lot_perc": 5, "quals_required": 2, "bust_lim": 2, "nde_types": ["RT"]}

Key rules preserved (see spec §10 Invariants):
  * Repair (R) rewelds are excluded.
  * Welds are ordered by date_welded then a stable tiebreak.
  * lot_size = round(100 / lot_perc); qualifying lot ("Q", number 0) when
    quals_required > 0, else numbering starts at 1.
  * A pending weld (no NDE result) counts toward lot size but is neither a
    pass (point) nor a fail (strike).
  * The final weld always closes its lot; no trailing empty lot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Optional


CLOSED = "closed"
OPEN = "open"


# --------------------------------------------------------------------------- #
# Settings                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class LotSettings:
    lot_perc: float = 5
    quals_required: int = 2
    bust_lim: int = 2
    nde_types: tuple = ("RT",)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LotSettings":
        d = d or {}
        nde = d.get("nde_types") or ["RT"]
        return cls(
            lot_perc=float(d.get("lot_perc", 5) or 5),
            quals_required=int(d.get("quals_required", 2) if d.get("quals_required") is not None else 2),
            bust_lim=int(d.get("bust_lim", 2) if d.get("bust_lim") is not None else 2),
            nde_types=tuple(str(t).upper() for t in nde),
        )

    @property
    def lot_size(self) -> int:
        return max(1, round(100 / self.lot_perc))


# --------------------------------------------------------------------------- #
# Result containers                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class WeldRow:
    """A weld enriched with its lot assignment and pass/fail verdict."""
    source: dict
    lot_label: str            # "Q" or "1", "2", ...
    lot_number: int           # 0 for qualifying lot
    last_in_lot: bool
    passed: bool              # has >= 1 matching passing NDE report
    failed: bool              # has >= 1 matching failing NDE report

    @property
    def pending(self) -> bool:
        return not self.passed and not self.failed


@dataclass
class Lot:
    label: str                # "Q" or "1", "2", ...
    number: int
    status: str               # CLOSED | OPEN
    welds: list = field(default_factory=list)   # list[WeldRow]
    points: int = 0           # passing welds
    strikes: int = 0          # failing welds

    @property
    def size(self) -> int:
        return len(self.welds)

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    @property
    def oldest_date(self) -> Optional[datetime]:
        ds = [_parse_dt(w.source.get("date_welded")) for w in self.welds]
        ds = [d for d in ds if d is not None]
        return min(ds) if ds else None


@dataclass
class WelderLots:
    welder: dict
    lots: list = field(default_factory=list)     # list[Lot], chronological
    error: Optional[str] = None

    @property
    def open_lots(self):
        return [lot for lot in self.lots if lot.is_open]

    @property
    def open_count(self) -> int:
        return len(self.open_lots)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of a date/datetime; returns None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if s in ("", "0000-00-00", "0000-00-00 00:00:00"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: ISO parser (handles offsets / fractional seconds)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _is_repair(weld: dict) -> bool:
    code = str(weld.get("reweld_code", "") or "").strip().lower()
    return code == "r"


def _weld_verdict(weld: dict, nde_types: tuple):
    """Return (passed, failed) for a weld against the active NDE types.

    A weld passes if any matching report passes; fails if any matching report
    fails. The two are independent (a weld can have both a fail and a later
    pass); the lot math counts a passing weld as a point and a failing weld as
    a strike (see spec §4).
    """
    passed = failed = False
    reports = weld.get("nde") or weld.get("nde_reports") or []
    want = set(t.upper() for t in nde_types) if nde_types else None
    for r in reports:
        rtype = str(r.get("type") or r.get("nde_type") or "").upper()
        if want and rtype and rtype not in want:
            continue
        # Accept several truthy spellings of pass/fail.
        pf = r.get("pass")
        if pf is None:
            pf = r.get("pass_fail")
        if pf is None and "result" in r:
            pf = str(r["result"]).lower() in ("pass", "p", "true", "1", "accept", "accepted")
        if bool(pf):
            passed = True
        else:
            failed = True
    return passed, failed


# --------------------------------------------------------------------------- #
# Core acceptance logic (spec §4)                                             #
# --------------------------------------------------------------------------- #
def lot_logic(lot_size: int, points: int, strikes: int, bust_lim: int) -> str:
    """Regular-lot acceptance. First matching rule wins (spec §4.2)."""
    if points >= 1 and strikes < 1:
        return CLOSED
    if strikes <= bust_lim and strikes > 0 and points / strikes >= 2:
        return CLOSED
    if strikes > bust_lim and (points + strikes) < lot_size:
        return OPEN
    if (points + strikes) >= lot_size:
        return CLOSED
    return OPEN


def _qual_status(points: int, strikes: int, quals_required: int) -> str:
    """Qualifying-lot acceptance (spec §4.1)."""
    if strikes > 0:
        return OPEN
    if points >= quals_required:
        return CLOSED
    return OPEN


# --------------------------------------------------------------------------- #
# Lot construction (spec §3, §9)                                              #
# --------------------------------------------------------------------------- #
def compute_lots(welds, settings, welder=None) -> WelderLots:
    """Group a welder's welds into lots and evaluate each lot.

    Parameters
    ----------
    welds : iterable of dict
        The welder's candidate welds (already attributed to the welder; any
        upstream joint/loc/wps/percentage filtering having been applied). Repair
        rewelds and welds before ``start_date`` are removed here.
    settings : LotSettings | dict
        Lot parameters.
    welder : dict, optional
        Welder metadata (wid, name, start_date). ``start_date`` is applied as a
        hard cutoff.

    Returns
    -------
    WelderLots
    """
    if not isinstance(settings, LotSettings):
        settings = LotSettings.from_dict(settings)
    welder = welder or {}
    result = WelderLots(welder=welder)

    start_cut = _parse_dt(welder.get("start_date"))

    # --- eligibility: drop repairs, undated, and pre-start-date welds -------- #
    eligible = []
    for w in welds:
        if _is_repair(w):
            continue
        dt = _parse_dt(w.get("date_welded"))
        if dt is None:
            continue  # undated welds cannot be ordered into lots
        if start_cut is not None and dt <= start_cut:
            continue
        eligible.append((dt, w))

    if not eligible:
        result.error = "No lots found"
        return result

    # --- order strictly by date, then stable weld-number tiebreak ------------ #
    eligible.sort(key=lambda pair: (pair[0], str(pair[1].get("weld_number", ""))))
    ordered = [w for _, w in eligible]

    lot_size = settings.lot_size
    qreq = settings.quals_required
    n_welds = len(ordered)

    # --- assign lot numbers + last_in_lot flags (spec §3.4) ------------------ #
    lot_no = 0 if qreq > 0 else 1
    rows: list[WeldRow] = []
    for i, w in enumerate(ordered):
        passed, failed = _weld_verdict(w, settings.nde_types)
        last = False
        n = i + 1
        if n >= qreq and (n % lot_size) == (qreq % lot_size):
            last = True
        rows.append(WeldRow(
            source=w,
            lot_label="Q" if lot_no == 0 else str(lot_no),
            lot_number=lot_no,
            last_in_lot=last,
            passed=passed,
            failed=failed,
        ))
        if last and (i + 1) < n_welds:
            lot_no += 1
    rows[-1].last_in_lot = True  # final weld always closes its lot

    # --- walk rows, accumulate points/strikes, close lots (spec §4) ---------- #
    lots: list[Lot] = []
    points = strikes = 0
    current: list[WeldRow] = []
    for row in rows:
        current.append(row)
        if row.passed:
            points += 1
        if row.failed:
            strikes += 1
        if row.last_in_lot:
            if row.lot_number == 0:
                status = _qual_status(points, strikes, qreq)
            else:
                status = lot_logic(len(current), points, strikes, settings.bust_lim)
            # stamp status back onto each weld row for easy reporting
            lot = Lot(
                label=row.lot_label,
                number=row.lot_number,
                status=status,
                welds=current,
                points=points,
                strikes=strikes,
            )
            lots.append(lot)
            points = strikes = 0
            current = []

    result.lots = lots
    return result


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal sanity checks against the spec's stated behavior.
    s = LotSettings(lot_perc=20, quals_required=2, bust_lim=2)  # lot_size = 5
    assert s.lot_size == 5

    def weld(n, day, result=None):
        w = {"weld_number": f"W-{n}", "date_welded": f"2026-01-{day:02d}"}
        if result is not None:
            w["nde"] = [{"type": "RT", "pass": result, "report": f"R-{n}", "date": f"2026-01-{day:02d}"}]
        return w

    # Qual lot (2 welds) both pass -> closed; then a regular lot of 5.
    welds = [
        weld(1, 1, True), weld(2, 2, True),           # Q -> closed
        weld(3, 3, True), weld(4, 4, True), weld(5, 5, True),
        weld(6, 6, True), weld(7, 7, True),           # lot 1 (5 welds) -> closed
    ]
    r = compute_lots(welds, s, {"wid": "12", "name": "Test"})
    labels = [(l.label, l.status, l.size) for l in r.lots]
    print("lots:", labels)
    assert labels[0] == ("Q", CLOSED, 2), labels
    assert labels[1] == ("1", CLOSED, 5), labels

    # Qual lot with a failure stays open regardless of bust limit.
    r2 = compute_lots([weld(1, 1, True), weld(2, 2, False)], s, {})
    assert r2.lots[0].status == OPEN, r2.lots[0].status

    # lot_logic spot checks
    assert lot_logic(5, 3, 0, 2) == CLOSED
    assert lot_logic(5, 4, 2, 2) == CLOSED     # 4 pass / 2 fail, within bust, ratio>=2
    assert lot_logic(5, 1, 3, 2) == OPEN       # strikes>bust, not shot out
    assert lot_logic(5, 2, 3, 2) == CLOSED     # 5 welds all examined -> closed
    print("welder_lots.py self-test: OK")
