import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair import algorithms as alg
from eeg_repair.cli import filter_main
from eeg_repair.config import Config
from eeg_repair.dataset import filter_dataset
from eeg_repair.pipeline import filter_file

NAMES = ("Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4")


def _recording(tmp_path):
    t = np.arange(3000) / 250
    signals = np.zeros((len(NAMES), t.size), dtype=np.float32)
    signals[1] = 80 * np.sin(2 * np.pi * 10 * t)
    signals[2] = 40 * np.sin(2 * np.pi * 50 * t) + 25
    table = pa.table(
        {
            "timestamp": t,
            "episode_index": np.zeros(t.size, dtype="int64"),
            **dict(zip(NAMES, signals)),
            "TRIG": np.arange(t.size, dtype="float32"),
        }
    )
    path = tmp_path / "observation.eeg.parquet"
    pq.write_table(table, path)
    cfg = Config(channels=(*NAMES, "TRIG"), sfreq=250, unit="uV")
    return cfg, path, table


def _forbid(name):
    def fail(*args, **kwargs):
        raise AssertionError(name)

    return fail


def test_filter_file_bandpass_notch_only(tmp_path, monkeypatch):
    cfg, path, source = _recording(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(alg, "remove_dc", _forbid("remove_dc"))
    monkeypatch.setattr(alg, "interpolate_bads", _forbid("interpolate"))
    monkeypatch.setattr(alg, "average_reference", _forbid("average_reference"))
    monkeypatch.setattr("eeg_repair.pipeline.prepare_montage", _forbid("montage"))
    output = tmp_path / "observation.eeg_denoise.parquet"
    result = filter_file(path, output, cfg)
    assert result["status"] == "SUCCESS"
    assert path.read_bytes() == original
    got = pq.read_table(output)
    assert got.schema.equals(source.schema, check_metadata=True)
    assert got["TRIG"].equals(source["TRIG"])
    signal = np.stack([got[name].to_numpy() for name in NAMES])
    assert np.max(np.abs(signal[0])) < 1e-4
    assert np.std(signal[1]) > 30
    assert np.std(signal[2]) < 8
    assert np.max(np.abs(signal.mean(0))) > 1


def _dataset(root):
    (root / "meta").mkdir(parents=True)
    names = list(NAMES)
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
                **{name: 20 * rng.normal(size=len(t)) for name in names},
            }
        ),
        path,
    )
    (root / "video.mp4").write_bytes(b"video fixture")
    (root / "meta/episodes_stats.jsonl").write_text(
        json.dumps({"episode_index": 0, "stats": {"observation.eeg": {"mean": [999]}}}) + "\n"
    )
    return path, info


def test_filter_dataset_publishes_feature_without_quality(tmp_path):
    root = tmp_path / "input"
    path, original = _dataset(root)
    original_bytes = path.read_bytes()
    original_stats = (root / "meta/episodes_stats.jsonl").read_bytes()
    assert filter_dataset(root, [path])["status"] == "SUCCESS"
    assert path.read_bytes() == original_bytes
    updated = json.loads((root / "meta/info.json").read_text())
    assert updated["features"]["observation.eeg"] == original["features"]["observation.eeg"]
    assert updated["data_path"] == original["data_path"]
    denoised = updated["features"]["observation.eeg_denoise"]
    assert denoised["names"] == original["features"]["observation.eeg"]["names"]
    assert denoised["unit"] == "uV"
    assert "bad_rate" not in denoised
    assert "eeg_repair" not in updated
    target = path.with_name("observation.eeg_denoise.parquet")
    assert target.exists()
    assert (root / "video.mp4").read_bytes() == b"video fixture"
    assert (root / "meta/episodes_stats.jsonl").read_bytes() == original_stats


def test_filter_dataset_failure_and_dry_run_publish_nothing(tmp_path, monkeypatch):
    root = tmp_path / "input"
    path, _ = _dataset(root)
    original_info = (root / "meta/info.json").read_bytes()
    original_eeg = path.read_bytes()
    assert filter_dataset(root, [path], dry_run=True)["status"] == "VALIDATED"
    assert not path.with_name("observation.eeg_denoise.parquet").exists()

    def fail(*args, **kwargs):
        if not kwargs.get("dry_run"):
            raise ValueError("filter failed")

    monkeypatch.setattr("eeg_repair.dataset.filter_file", fail)
    with pytest.raises(ValueError, match="filter failed"):
        filter_dataset(root, [path])
    assert path.read_bytes() == original_eeg
    assert (root / "meta/info.json").read_bytes() == original_info
    assert not path.with_name("observation.eeg_denoise.parquet").exists()
    assert not list(root.glob(".eeg-dataset-*"))
    with pytest.raises(ValueError, match="all EEG"):
        filter_dataset(root, [])


def test_filter_batch_isolates_failures(tmp_path, monkeypatch, capsys):
    root = tmp_path / "input"
    for name in ("episode_001", "episode_002"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "observation.eeg.parquet").touch()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("channels: [Fp1, Fp2, C3, C4]\nsfreq: 250\nunit: uV\n")
    calls = []

    def run(source, destination, config, **kwargs):
        calls.append((source, destination))
        if source.parent.name == "episode_001":
            raise ValueError("bad source")
        return {"status": "SUCCESS"}

    monkeypatch.setattr("eeg_repair.pipeline.filter_file", run)
    output = tmp_path / "output"
    assert filter_main([str(root), "--config", str(cfg), "--output-dir", str(output)]) == 1
    text = "".join(capsys.readouterr())
    assert "成功" in text
    assert "失败" in text
    assert "完成：成功 1，失败 1" in text
    assert len(calls) == 2
    assert calls[1][1] == output / "episode_002" / "observation.eeg_denoise.parquet"
    record = json.loads((output / "quarantine.jsonl").read_text())
    assert "bad source" in record["error"]
