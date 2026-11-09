"""Read-only schema inspection and configuration scaffolding for new devices."""

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import yaml


def inspect_file(path: Path) -> dict:
    path = path.resolve()
    parquet = pq.ParquetFile(path)
    from .metadata import read_metadata

    metadata = read_metadata(path)
    sample_rate = None
    if "timestamp" in parquet.schema_arrow.names and parquet.metadata.num_rows > 1:
        stamps = parquet.read(columns=["timestamp"])["timestamp"].to_numpy()
        delta = np.diff(stamps)
        if np.isfinite(delta).all() and np.all(delta > 0):
            sample_rate = float(1 / np.median(delta))
    return {
        "source": str(path),
        "rows": parquet.metadata.num_rows,
        "row_groups": parquet.num_row_groups,
        "columns": [{"name": f.name, "type": str(f.type)} for f in parquet.schema_arrow],
        "metadata_path": metadata.get("metadata_path"),
        "channels": metadata.get("channels"),
        "metadata_sfreq": metadata.get("sfreq"),
        "timestamp_sfreq": sample_rate,
        "unit": metadata.get("unit"),
        "device": metadata.get("device"),
        "note": "Units and electrode coordinates must be confirmed; no amplitude-based guesses.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only EEG Parquet schema inspection")
    parser.add_argument("input", type=Path)
    parser.add_argument("--write-config", type=Path, help="create a template; refuses to overwrite")
    args = parser.parse_args(argv)
    result = inspect_file(args.input)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write_config:
        cfg = {
            "channels": result["channels"] or [],
            "sfreq": result["metadata_sfreq"] or result["timestamp_sfreq"],
            "unit": result["unit"],
            "layout": "columns",
            "montage": "standard_1005",
            "montage_csv": None,
        }
        with args.write_config.open("x", encoding="utf-8") as stream:
            stream.write(
                "# Confirm unit and montage before running; null unit intentionally blocks repair.\n"
            )
            yaml.safe_dump(cfg, stream, sort_keys=False, allow_unicode=True)
    return 0
