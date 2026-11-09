"""Read per-recording mf-LeRobot metadata without guessing physical units."""

import json
from pathlib import Path

import pyarrow.parquet as pq


def read_metadata(path: str | Path) -> dict:
    path = Path(path).resolve()
    candidate = next((p / "meta/info.json" for p in path.parents if (p / "meta/info.json").exists()), None)
    if candidate is None:
        return {}
    info = json.loads(candidate.read_text(encoding="utf-8"))
    feature = info.get("features", {}).get("observation.eeg", {})
    result = {
        "metadata_path": str(candidate),
        "channels": feature.get("names"),
        "sfreq": feature.get("fps"),
        "unit": feature.get("unit"),
        "shape": feature.get("shape"),
    }
    collect = candidate.with_name("collect_info.jsonl")
    if collect.exists():
        parquet = pq.ParquetFile(path)
        if "episode_index" in parquet.schema_arrow.names:
            episodes = parquet.read(columns=["episode_index"])["episode_index"].unique().to_pylist()
            if len(episodes) != 1:
                raise ValueError("require exactly one episode_index per recording")
            matches = [
                json.loads(line) for line in collect.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            matches = [row for row in matches if row.get("episode_index") == episodes[0]]
            if len(matches) > 1:
                raise ValueError("duplicate collect_info episode_index")
            if matches:
                result.update(
                    device=matches[0].get("hardware", {}).get("eeg"),
                    collect_version=matches[0].get("collect_version"),
                    collect_metadata_path=str(collect),
                )
    return result
