import json

import pyarrow as pa
import pyarrow.parquet as pq

from eeg_repair.cli import main
from eeg_repair.inspection import inspect_file


def test_batch_isolates_failures_and_preserves_episode_paths(tmp_path, monkeypatch):
    root = tmp_path / "input"
    for name in ("episode_001", "episode_002"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "observation.eeg.parquet").touch()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("channels: [Fp1, Fp2, C3, C4]\nsfreq: 250\nunit: uV\n")
    calls = []

    def repair(source, destination, config, **kwargs):
        calls.append((source, destination))
        if source.parent.name == "episode_001":
            raise ValueError("bad source")
        return {"status": "SUCCESS"}

    monkeypatch.setattr("eeg_repair.pipeline.repair_file", repair)
    output = tmp_path / "output"
    assert main([str(root), "--config", str(cfg), "--output-dir", str(output)]) == 1
    assert len(calls) == 2
    assert calls[1][1] == output / "episode_002" / "observation.eeg_repair.parquet"
    record = json.loads((output / "quarantine.jsonl").read_text())
    assert "bad source" in record["error"]
    assert all(source.exists() for source, _ in calls)


def test_inspection_uses_dataset_metadata_and_does_not_guess_units(tmp_path):
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "info.json").write_text(
        json.dumps({"features": {"observation.eeg": {"names": ["Fp1"], "fps": 250.0}}})
    )
    data = tmp_path / "data"
    data.mkdir()
    path = data / "observation.eeg.parquet"
    pq.write_table(pa.table({"timestamp": [0.0, 0.004, 0.008], "Fp1": [1.0, 2.0, 3.0]}), path)
    result = inspect_file(path)
    assert result["channels"] == ["Fp1"]
    assert result["unit"] is None
    assert result["timestamp_sfreq"] == 250.0
