"""Structural QC for EEG Parquet: empty files, nulls, non-finite values, and missing frames."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

REPAIR_NAME = "observation.eeg_repair.parquet"
_GAP_STEPS = 1.5


def discover(source: Path, pattern: str | None = None) -> list[Path]:
    source = source.resolve()
    if source.is_file():
        return [source]
    return sorted(source.rglob(pattern or REPAIR_NAME))


def check_recording(path: str | Path) -> dict:
    """Return a PASS/FAIL report. The file is only read."""
    path = Path(path).resolve()
    report = {
        "path": str(path),
        "status": "FAIL",
        "rows": 0,
        "channels": 0,
        "null_count": 0,
        "non_finite_count": 0,
        "duplicate_timestamps": 0,
        "missing_frames": 0,
        "issues": [],
    }
    if not path.is_file():
        report["issues"].append({"code": "missing_file", "count": 1, "detail": "input file does not exist"})
        return report
    try:
        parquet = pq.ParquetFile(path)
    except (OSError, pa.ArrowInvalid, pa.ArrowException) as exc:
        report["issues"].append({"code": "unreadable", "count": 1, "detail": f"{type(exc).__name__}: {exc}"})
        return report
    report["rows"] = int(parquet.metadata.num_rows)
    if report["rows"] == 0:
        report["issues"].append({"code": "empty", "count": 1, "detail": "parquet has no rows"})
        return report

    from .metadata import read_metadata

    metadata = read_metadata(path)
    names = parquet.schema_arrow.names
    declared = list(metadata.get("channels") or [])
    missing = [name for name in declared if name not in names]
    if missing:
        report["issues"].append(
            {"code": "missing_channel", "count": len(missing), "detail": ", ".join(missing)}
        )
    signal_names = declared or [
        field.name
        for field in parquet.schema_arrow
        if pa.types.is_floating(field.type) and field.name != "timestamp"
    ]
    report["channels"] = len(signal_names)

    nulls = 0
    non_finite = 0
    for name in signal_names:
        if name not in names:
            continue
        column = parquet.read(columns=[name])[name]
        nulls += int(column.null_count)
        if pa.types.is_floating(column.type):
            values = column.drop_null().to_numpy(zero_copy_only=False)
            if values.size:
                non_finite += int(np.count_nonzero(~np.isfinite(values)))
    report["null_count"] = nulls
    report["non_finite_count"] = non_finite
    if nulls:
        report["issues"].append({"code": "null", "count": nulls, "detail": "EEG column contains nulls"})
    if non_finite:
        report["issues"].append(
            {"code": "non_finite", "count": non_finite, "detail": "EEG column contains NaN or Inf"}
        )

    if "timestamp" not in names:
        report["issues"].append(
            {"code": "missing_timestamp", "count": 1, "detail": "timestamp column is absent"}
        )
    else:
        stamps_col = parquet.read(columns=["timestamp"])["timestamp"]
        if not pa.types.is_float64(stamps_col.type):
            report["issues"].append(
                {
                    "code": "timestamp_dtype",
                    "count": 1,
                    "detail": "timestamps must be float64 seconds",
                }
            )
        if stamps_col.null_count or not pa.types.is_floating(stamps_col.type):
            report["issues"].append(
                {
                    "code": "timestamp_invalid",
                    "count": int(stamps_col.null_count) or 1,
                    "detail": "timestamps must be non-null floating point values",
                }
            )
        elif pa.types.is_floating(stamps_col.type):
            stamps = stamps_col.to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
            negative = int(np.count_nonzero(np.isfinite(stamps) & (stamps < 0)))
            if negative:
                report["issues"].append(
                    {
                        "code": "timestamp_negative",
                        "count": negative,
                        "detail": "timestamps must be non-negative seconds",
                    }
                )
            if not np.isfinite(stamps).all():
                report["issues"].append(
                    {
                        "code": "timestamp_invalid",
                        "count": int(np.size(stamps) - np.isfinite(stamps).sum()),
                        "detail": "timestamps must be finite",
                    }
                )
            elif stamps.size > 1:
                delta = np.diff(stamps)
                duplicates = int(np.count_nonzero(delta == 0))
                reversals = int(np.count_nonzero(delta < 0))
                report["duplicate_timestamps"] = duplicates
                if duplicates:
                    report["issues"].append(
                        {"code": "timestamp_duplicate", "count": duplicates, "detail": "repeated timestamps"}
                    )
                if reversals:
                    report["issues"].append(
                        {"code": "timestamp_reversal", "count": reversals, "detail": "timestamps decrease"}
                    )
                sfreq = metadata.get("sfreq")
                positive = delta[delta > 0]
                if sfreq is None and positive.size:
                    sfreq = float(1.0 / np.median(positive))
                if sfreq and positive.size:
                    step = 1.0 / float(sfreq)
                    gaps = delta > (_GAP_STEPS * step)
                    if np.any(gaps):
                        omitted = np.rint(delta[gaps] / step).astype(np.int64) - 1
                        missing_frames = int(omitted[omitted > 0].sum())
                    else:
                        missing_frames = 0
                    report["missing_frames"] = missing_frames
                    if missing_frames:
                        report["issues"].append(
                            {
                                "code": "missing_frames",
                                "count": missing_frames,
                                "detail": f"timestamp gaps exceed {_GAP_STEPS:g} samples at {float(sfreq):.6g} Hz",
                            }
                        )
    if "episode_index" not in names:
        report["issues"].append(
            {"code": "missing_episode_index", "count": 1, "detail": "episode_index column is absent"}
        )
    else:
        episodes = parquet.read(columns=["episode_index"])["episode_index"]
        if not pa.types.is_int64(episodes.type) or episodes.null_count:
            report["issues"].append(
                {
                    "code": "episode_index_invalid",
                    "count": int(episodes.null_count) or 1,
                    "detail": "episode_index must be non-null int64",
                }
            )
        elif len(episodes.unique().to_pylist()) != 1:
            report["issues"].append(
                {
                    "code": "episode_index_invalid",
                    "count": len(episodes.unique().to_pylist()),
                    "detail": "episode_index must be constant within a file",
                }
            )
    if not report["issues"]:
        report["status"] = "PASS"
    return report


def check_paths(paths: list[Path]) -> dict:
    results = [check_recording(path) for path in paths]
    failed = sum(item["status"] != "PASS" for item in results)
    return {
        "status": "PASS" if results and failed == 0 else "FAIL",
        "files": len(results),
        "failed": failed,
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check repaired EEG Parquet for nulls and missing frames")
    parser.add_argument("input", type=Path, help="repaired Parquet or a directory to search")
    parser.add_argument(
        "--pattern",
        default=REPAIR_NAME,
        help="filename glob used when input is a directory (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    source = args.input.resolve()
    if not source.exists():
        print(json.dumps({"status": "FAIL", "error": f"input does not exist: {source}"}, ensure_ascii=False))
        return 2
    paths = discover(source, None if source.is_file() else args.pattern)
    if not paths:
        print(
            json.dumps(
                {"status": "FAIL", "error": f"no files matched {args.pattern}", "files": 0, "failed": 0},
                ensure_ascii=False,
            )
        )
        return 2
    report = check_paths(paths)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1
