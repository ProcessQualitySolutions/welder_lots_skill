#!/usr/bin/env python3
"""
random_weld_select.py — lightweight random weld selection + audit logging.

Developed by the QCDatabase.AI team. Released as open source as part of the
Welder Lots System skill.

Purpose
-------
When statistical NDE (Non-Destructive Examination) sampling requires a *random*
weld to be examined, this script performs the draw and writes an audit-log line
recording what was picked, from which pool, and for whom. It does exactly two
things:

  1. Uniformly selects ONE weld from a supplied pool of weld ids/labels.
  2. Appends a human-readable audit line to a log file.

It deliberately does NOT talk to any quality/NDE system. After selection, the
agent (or a human) still submits the actual NDE request in whatever quality
system is in use. Keeping selection separate makes the random draw auditable,
reproducible (with a seed), and dependency-free.

Selection integrity
--------------------
By default the draw uses the operating-system CSPRNG (``secrets.choice``) so the
selection is defensible for quality records. Pass ``--seed`` to get a
reproducible draw (uses Python's deterministic PRNG) for testing.

Pool rule
---------
Statistical sampling needs a real pool: the script refuses to "select" from a
pool of fewer than 2 welds unless ``--allow-single`` is given. This mirrors the
Welder Lots System invariant that a random pool must contain >= 2 welds.

Usage
-----
  # Select one weld from a pool and log the draw
  python random_weld_select.py --log audit.log --welder "John Doe" --lot 3 \
      --nde RT W-1001 W-1042 W-1099

  # Read the pool from stdin (one weld per line, or comma/space separated)
  echo "W-1001 W-1042 W-1099" | python random_weld_select.py -l audit.log -w "John Doe" --lot 3

  # Reproducible draw for tests
  python random_weld_select.py -l /dev/null --seed 42 W-1 W-2 W-3

Example log line
----------------
  2026-07-11T14:03:22Z  W-1042 selected for RT from [W-1001, W-1042, W-1099] for John Doe lot 3 (sample size 3)

Exit codes
----------
  0  success (a weld was selected; the selected label is printed to stdout)
  2  usage / input error (empty pool, pool < 2 without --allow-single, etc.)
"""

from __future__ import annotations

import argparse
import secrets
import random
import sys
from datetime import datetime, timezone


__all__ = ["select_weld", "format_log_line", "main"]


def _utc_timestamp() -> str:
    """ISO-8601 UTC timestamp, second precision, e.g. 2026-07-11T14:03:22Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def select_weld(pool, seed=None):
    """Uniformly select one weld label from ``pool``.

    Parameters
    ----------
    pool : sequence of str
        The candidate weld ids/labels (already filtered down to eligible welds).
    seed : optional
        If provided, selection is deterministic (uses Python's PRNG) so tests
        are reproducible. If omitted, the OS CSPRNG is used.

    Returns
    -------
    str
        The selected weld label.

    Raises
    ------
    ValueError
        If ``pool`` is empty.
    """
    pool = list(pool)
    if not pool:
        raise ValueError("cannot select from an empty pool")
    if seed is not None:
        return random.Random(seed).choice(pool)
    return secrets.choice(pool)


def format_log_line(selected, pool, nde="RT", welder=None, lot=None, timestamp=None):
    """Build the audit-log line for a selection.

    Mirrors the shape:
      "<label> selected for <NDE> from [<list>] for <welder> lot <lot> (sample size N)"
    prefixed with a UTC timestamp. ``welder`` and ``lot`` are optional and are
    omitted gracefully when not supplied.
    """
    ts = timestamp or _utc_timestamp()
    pool_str = ", ".join(str(w) for w in pool)
    line = f"{selected} selected for {nde} from [{pool_str}]"
    if welder:
        line += f" for {welder}"
    if lot is not None and str(lot) != "":
        line += f" lot {lot}"
    line += f" (sample size {len(pool)})"
    return f"{ts}  {line}"


def _parse_pool(tokens):
    """Flatten CLI/stdin tokens into a clean list of weld labels.

    Accepts whitespace- and/or comma-separated values so callers can pass
    ``W-1 W-2``, ``W-1,W-2``, or lines from stdin interchangeably.
    """
    pool = []
    for tok in tokens:
        for part in str(tok).replace(",", " ").split():
            part = part.strip()
            if part:
                pool.append(part)
    return pool


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="random_weld_select.py",
        description="Randomly select one weld from a pool and log the draw "
                    "(Developed by the QCDatabase.AI team).",
    )
    parser.add_argument("welds", nargs="*",
                        help="Weld ids/labels forming the selection pool. "
                             "Space/comma separated. If omitted, read from stdin.")
    parser.add_argument("-l", "--log", required=True,
                        help="Path to the audit log file (line is appended).")
    parser.add_argument("-n", "--nde", default="RT",
                        help="NDE type the weld is selected for (default: RT).")
    parser.add_argument("-w", "--welder", default=None,
                        help="Welder name/id the lot belongs to.")
    parser.add_argument("--lot", default=None,
                        help="Lot number/label (e.g. 3, or Q for the qualifying lot).")
    parser.add_argument("--seed", default=None,
                        help="Optional seed for a reproducible draw (testing).")
    parser.add_argument("--allow-single", action="store_true",
                        help="Permit selection from a pool of a single weld "
                             "(by default a pool < 2 is refused).")
    parser.add_argument("--encoding", default="utf-8",
                        help="Log file encoding (default: utf-8).")
    args = parser.parse_args(argv)

    # Build the pool: CLI args take precedence, else read stdin.
    tokens = args.welds
    if not tokens and not sys.stdin.isatty():
        tokens = sys.stdin.read().splitlines()
    pool = _parse_pool(tokens)

    if not pool:
        parser.error("no welds supplied (pass them as arguments or via stdin)")
    if len(pool) < 2 and not args.allow_single:
        parser.error(
            f"pool has only {len(pool)} weld; statistical sampling needs >= 2 "
            f"(use --allow-single to override)")

    seed = None
    if args.seed is not None:
        # Keep numeric seeds numeric for stable behavior; fall back to string.
        try:
            seed = int(args.seed)
        except ValueError:
            seed = args.seed

    selected = select_weld(pool, seed=seed)
    line = format_log_line(
        selected=selected,
        pool=pool,
        nde=args.nde,
        welder=args.welder,
        lot=args.lot,
    )

    try:
        with open(args.log, "a", encoding=args.encoding) as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"ERROR: could not write log '{args.log}': {exc}", file=sys.stderr)
        return 2

    # Human-readable confirmation to stderr, machine-usable result to stdout.
    print(line, file=sys.stderr)
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
