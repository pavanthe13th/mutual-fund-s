#!/usr/bin/env python3
"""Local validation harness.

Run after every change to compare parser output against a saved baseline
snapshot. This is the cheap, deterministic local proxy for the hidden
evaluator — no /eval runs are spent here.

Usage:
    # First run on the current code (or after a known-good state):
    python3 local_validate.py --save-baseline

    # After making a change:
    python3 local_validate.py

The snapshot captures, per file, per sheet:
    - row count
    - column list (in order)
    - by-fund record counts
    - distinct (instrument_type, category, subcategory) tuples + counts
    - sample of first/last 3 rows
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = REPO_ROOT / "4_output_interview" / "DSP"
DEFAULT_BASELINE = REPO_ROOT / ".local_baseline.json"


def snapshot_file(xlsx_path: Path) -> dict:
    xl = pd.ExcelFile(xlsx_path)
    per_sheet = {}
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet)
        hier_dict = {}
        if "instrument_type" in df.columns:
            tuples = [
                tuple(str(v) if pd.notna(v) else "" for v in r)
                for r in df.reindex(
                    columns=["instrument_type", "category", "subcategory"]
                ).itertuples(index=False, name=None)
            ]
            for tup, cnt in Counter(tuples).items():
                hier_dict[" | ".join(tup)] = cnt
        per_sheet[sheet] = {
            "rows": int(len(df)),
            "cols": list(df.columns),
            "by_fund": (
                df["fund_code"].fillna("__NA__").value_counts().sort_index().to_dict()
                if "fund_code" in df.columns
                else {}
            ),
            "by_hier": hier_dict,
            "head3": df.head(3).fillna("").astype(str).to_dict(orient="records"),
            "tail3": df.tail(3).fillna("").astype(str).to_dict(orient="records"),
        }
    return per_sheet


def build_snapshot(output_dir: Path) -> dict:
    snapshot = {}
    for f in sorted(output_dir.glob("*_parsed.xlsx")):
        snapshot[f.name] = snapshot_file(f)
    return snapshot


def diff_snapshots(before: dict, after: dict) -> str:
    lines = []
    files = sorted(set(before) | set(after))
    for fname in files:
        b = before.get(fname)
        a = after.get(fname)
        if b is None:
            lines.append(f"+ NEW FILE: {fname}")
            continue
        if a is None:
            lines.append(f"- REMOVED FILE: {fname}")
            continue
        if b == a:
            continue
        lines.append(f"\n## {fname}")
        sheets = sorted(set(b) | set(a))
        for sheet in sheets:
            sb = b.get(sheet, {})
            sa = a.get(sheet, {})
            if sb == sa:
                continue
            lines.append(f"  sheet: {sheet}")
            if sb.get("rows") != sa.get("rows"):
                delta = sa.get("rows", 0) - sb.get("rows", 0)
                lines.append(
                    f"    rows: {sb.get('rows')} -> {sa.get('rows')} (delta {delta:+d})"
                )
            if sb.get("cols") != sa.get("cols"):
                lines.append("    cols changed:")
                lines.append(f"      before: {sb.get('cols')}")
                lines.append(f"      after : {sa.get('cols')}")
            if sb.get("by_fund") != sa.get("by_fund"):
                bf, af = sb.get("by_fund", {}), sa.get("by_fund", {})
                funds = sorted(set(bf) | set(af))
                diffs = []
                for fc in funds:
                    if bf.get(fc) != af.get(fc):
                        diffs.append(f"{fc}: {bf.get(fc, 0)} -> {af.get(fc, 0)}")
                if diffs:
                    lines.append("    by_fund: " + "; ".join(diffs))
            if sb.get("by_hier") != sa.get("by_hier"):
                bh = sb.get("by_hier", {})
                ah = sa.get("by_hier", {})
                added = {k: ah[k] for k in ah if k not in bh}
                removed = {k: bh[k] for k in bh if k not in ah}
                changed = {
                    k: (bh[k], ah[k]) for k in ah if k in bh and ah[k] != bh[k]
                }
                if added:
                    lines.append("    + new hier tuples:")
                    for k, v in added.items():
                        lines.append(f"        {k}: {v}")
                if removed:
                    lines.append("    - removed hier tuples:")
                    for k, v in removed.items():
                        lines.append(f"        {k}: {v}")
                if changed:
                    lines.append("    ~ changed hier tuples:")
                    for k, (bv, av) in changed.items():
                        lines.append(f"        {k}: {bv} -> {av}")
    if not lines:
        return "No changes vs baseline."
    return "\n".join(lines)


def print_summary(snap: dict) -> None:
    print("\n=== SUMMARY ===")
    for fname, sheets in snap.items():
        all_data = sheets.get("All Data", {})
        rows = all_data.get("rows", 0)
        cols = all_data.get("cols", [])
        print(f"  {fname}: rows={rows}, cols={len(cols)}")
        funds = all_data.get("by_fund", {})
        prefix = ", ".join(f"{k}:{v}" for k, v in list(funds.items())[:6])
        suffix = "..." if len(funds) > 6 else ""
        print(f"    funds={len(funds)} | totals: {prefix}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local validation harness")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()

    if not args.output_dir.exists():
        print(f"Output dir {args.output_dir} not found - run interview_parse first")
        return 1

    snap = build_snapshot(args.output_dir)
    print_summary(snap)

    if args.save_baseline:
        args.baseline.write_text(json.dumps(snap, default=str, indent=2))
        print(f"\nBaseline saved to {args.baseline}")
        return 0

    if not args.baseline.exists():
        print(f"\nNo baseline at {args.baseline}. Run with --save-baseline first.")
        return 0

    before = json.loads(args.baseline.read_text())
    print("\n=== DIFF vs BASELINE ===")
    print(diff_snapshots(before, snap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
