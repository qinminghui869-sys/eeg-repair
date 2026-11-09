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
    out = tmp_path / "out"
    assert main([str(root), "--output-dir", str(out), "--config", str(config)]) == 0
    updated = json.loads((out / "meta/info.json").read_text())
    assert updated["features"] == original["features"]
    assert updated["data_path"] == original["data_path"]
    assert updated["eeg_repair"]["status"] == "complete"
    target = out / path.relative_to(root)
    assert target.exists()
    assert (out / "meta/observation.eeg.audit.json").exists()
    assert not target.with_suffix(".audit.json").exists()
    assert updated["eeg_repair"]["records"][0]["audit"] == "meta/observation.eeg.audit.json"
    assert (out / "video.mp4").read_bytes() == b"video fixture"
    assert json.loads((root / "meta/info.json").read_text()) == original
    stats = json.loads((out / "meta/episodes_stats.jsonl").read_text())["stats"]
    values = np.stack(
        [pq.read_table(target)[n].to_numpy() for n in original["features"]["observation.eeg"]["names"]],
        axis=1,
    )
    np.testing.assert_allclose(stats["observation.eeg"]["mean"], values.mean(axis=0))
    assert stats["other"] == {"count": [10]}


def test_failure_and_dry_run_publish_nothing(tmp_path, monkeypatch):
    root = tmp_path / "input"
    path, _ = fixture(root)
    out = tmp_path / "out"
    repair_dataset(root, out, [path], dry_run=True)
    assert not out.exists()

    def fail(*args, **kwargs):
        if not kwargs.get("dry_run"):
            raise ValueError("repair failed")

    monkeypatch.setattr("eeg_repair.dataset.repair_file", fail)
    with pytest.raises(ValueError, match="repair failed"):
        repair_dataset(root, out, [path])
    assert not out.exists()
    assert not list(tmp_path.glob(".eeg-dataset-*"))
    with pytest.raises(ValueError, match="all EEG"):
        repair_dataset(root, out, [])
