#!/usr/bin/env python3
"""修正 BrainCo parquet EEG 通道列名到硬件标准序（只改名，不重排数据；直接覆盖原文件）。

32ch: P8..Fz（含 IO）
33ch: TRIG + 上述 32 名

用法:
  python scripts/fix_brainco_parquet_channel_names.py path/to/xxx.eeg.parquet
  python scripts/fix_brainco_parquet_channel_names.py path/to/xxx.eeg.parquet --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

META = ("timestamp", "episode_index")
HW32 = [
    "P8",
    "P7",
    "T8",
    "T7",
    "F8",
    "F7",
    "O2",
    "O1",
    "P4",
    "P3",
    "C4",
    "C3",
    "F4",
    "F3",
    "Fp2",
    "Fp1",
    "TP10",
    "TP9",
    "FT10",
    "FT9",
    "CP6",
    "CP5",
    "FC6",
    "FC5",
    "CP2",
    "CP1",
    "FC2",
    "FC1",
    "IO",
    "Pz",
    "Cz",
    "Fz",
]
HW33 = ["TRIG", *HW32]


def canon(name: str) -> str:
    s = str(name).strip()
    return ("Fp" + s[2:]) if len(s) >= 2 and s[:2].upper() == "FP" else s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("parquet", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    path: Path = args.parquet
    if not path.is_file():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 1

    table = pq.read_table(path)
    cols = list(table.column_names)
    meta = [c for c in META if c in cols]
    chans = [c for c in cols if c not in META]
    if len(chans) == 32:
        expect = HW32
    elif len(chans) == 33:
        expect = HW33
    else:
        print(f"ERROR: need 32/33 channels, got {len(chans)}", file=sys.stderr)
        return 1

    print(f"file: {path}")
    print(f"rows={table.num_rows}  n_ch={len(chans)}")
    print("current :", chans)
    print("expected:", expect)

    if chans == expect:
        print("status: OK")
        return 0

    for i, (a, b) in enumerate(zip(chans, expect)):
        if canon(a) != canon(b) or a != b:
            print(f"  [{i:02d}] {a} -> {b}")

    if args.dry_run:
        print("status: DRY-RUN")
        return 0

    arrays = [table.column(c).combine_chunks() for c in meta + chans]
    fixed = pa.Table.from_arrays(arrays, names=list(meta) + expect)
    pq.write_table(fixed, path, compression="snappy")
    print(f"status: OVERWRITTEN -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
