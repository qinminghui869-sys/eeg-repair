import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair.dataset import repair_dataset
from eeg_repair.cli import main


def fixture(root):
    (root / "meta").mkdir(parents=True)
    names = ["Fp1", "Fp2", "C3", "C4", "P3", "P4", "O1", "O2"]
    info = {
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}/{feature_key}.parquet",
        "total_episodes": 1,
        "features": {
            "observation.eeg": {"names": names, "shape": [8], "fps": 250.0, "dtype": "float64", "unit": "uV"}
        },
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    path = root / "data/chunk-000/episode_000000/observation.eeg.parquet"
    path.parent.mkdir(parents=True)
    t = np.arange(3000) / 250
    rng = np.random.default_rng(5)
    pq.write_table(
        pa.table(
            {
                "timestamp": t,
                "episode_index": np.zeros(len(t), dtype=int),
                **{n: 20 * rng.normal(size=len(t)) for n in names},
            }
        ),
        path,
    )
    (root / "video.mp4").write_bytes(b"video fixture")
    (root / "meta/episodes_stats.jsonl").write_text(
        json.dumps(
            {"episode_index": 0, "stats": {"observation.eeg": {"mean": [999]}, "other": {"count": [10]}}}
        )
        + "\n"
    )
    return path, info


def test_complete_dataset_and_fresh_stats(tmp_path):
    root = tmp_path / "input"
    path, original = fixture(root)
    config = tmp_path / "cfg.yaml"
    config.write_text("ica_enabled: false\n")
    original_bytes = path.read_bytes()
    original_stats = (root / "meta/episodes_stats.jsonl").read_bytes()
    out = tmp_path / "out"
    assert main([str(root), "--output-dir", str(out), "--config", str(config)]) == 0
    assert path.read_bytes() == original_bytes
    assert not out.exists()
    updated = json.loads((root / "meta/info.json").read_text())
    assert updated["features"]["observation.eeg"] == original["features"]["observation.eeg"]
    repaired = updated["features"]["observation.eeg_repair"]
    assert repaired["names"] == original["features"]["observation.eeg"]["names"]
    assert repaired["unit"] == "uV"
    assert updated["data_path"] == original["data_path"]
    assert updated["eeg_repair"]["status"] == "complete"
    assert updated["eeg_repair"]["feature"] == "observation.eeg_repair"
    target = path.with_name("observation.eeg_repair.parquet")
    assert target.exists()
    assert not (root / "meta/observation.eeg.audit.json").exists()
    assert not target.with_suffix(".audit.json").exists()
    assert updated["eeg_repair"]["records"][0]["data"].endswith("observation.eeg_repair.parquet")
    assert "audit" not in updated["eeg_repair"]["records"][0]
    assert (root / "video.mp4").read_bytes() == b"video fixture"
    assert (root / "meta/episodes_stats.jsonl").read_bytes() == original_stats


def test_failure_and_dry_run_publish_nothing(tmp_path, monkeypatch):
    root = tmp_path / "input"
    path, _ = fixture(root)
    original_info = (root / "meta/info.json").read_bytes()
    original_eeg = path.read_bytes()
    repair_dataset(root, [path], dry_run=True)
    assert not path.with_name("observation.eeg_repair.parquet").exists()

    def fail(*args, **kwargs):
        if not kwargs.get("dry_run"):
            raise ValueError("repair failed")

    monkeypatch.setattr("eeg_repair.dataset.repair_file", fail)
    with pytest.raises(ValueError, match="repair failed"):
        repair_dataset(root, [path])
    assert path.read_bytes() == original_eeg
    assert (root / "meta/info.json").read_bytes() == original_info
    assert not path.with_name("observation.eeg_repair.parquet").exists()
    assert not list(root.glob(".eeg-dataset-*"))
    with pytest.raises(ValueError, match="all EEG"):
        repair_dataset(root, [])
