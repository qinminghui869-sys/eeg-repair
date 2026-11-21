import importlib.util
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts/rerun_brainco_repair.py"
    spec = importlib.util.spec_from_file_location("rerun_brainco_repair", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rerun = _load()
RECORDED_33 = [
    "FP1",
    "FP2",
    "F3",
    "F4",
    "F7",
    "F8",
    "Fz",
    "C3",
    "C4",
    "Cz",
    "P3",
    "P4",
    "P7",
    "P8",
    "Pz",
    "O1",
    "O2",
    "T7",
    "T8",
    "FC1",
    "FC2",
    "FC5",
    "FC6",
    "CP1",
    "CP2",
    "CP5",
    "CP6",
    "FT9",
    "FT10",
    "TP9",
    "TP10",
    "IO",
    "TRIG",
]


def _session(root: Path, channels: list[str], *, repair: bool = True) -> Path:
    (root / "meta").mkdir(parents=True)
    info = {
        "data_path": "data/chunk-000/episode_{episode_index:06d}/observation.eeg.parquet",
        "features": {
            "observation.eeg": {
                "dtype": "float32",
                "fps": 250.0,
                "names": RECORDED_33[: len(channels)]
                if len(channels) == 33
                else [f"OLD{i}" for i in range(len(channels))],
                "shape": [len(channels)],
                "unit": None,
            }
        },
        "eeg_repair": {"quality": "excellent", "records": [{"quality": "excellent"}]},
    }
    if len(channels) == 33:
        info["features"]["observation.eeg"]["names"] = list(RECORDED_33)
    info["features"]["observation.eeg_repair"] = {"names": ["FP1"], "shape": [1], "unit": None}
    (root / "meta/info.json").write_text(json.dumps(info))
    path = root / "data/chunk-000/episode_000000/observation.eeg.parquet"
    path.parent.mkdir(parents=True)
    columns = {
        "timestamp": pa.array([0.0, 0.004], type=pa.float64()),
        "episode_index": pa.array([0, 0], type=pa.int64()),
        **{name: pa.array([float(i), float(i) + 1], type=pa.float32()) for i, name in enumerate(channels)},
    }
    pq.write_table(pa.table(columns), path)
    if repair:
        repair_path = path.with_name("observation.eeg_repair.parquet")
        repair_path.write_bytes(b"previous-repair")
    return path


def test_rerun_updates_info_removes_old_repair_and_calls_repair(tmp_path, monkeypatch):
    path = _session(tmp_path / "session", rerun.HW33)
    seen = {}

    def fake_repair(root, paths, config=None, **kwargs):
        info = json.loads((root / "meta/info.json").read_text())
        feature = info["features"]["observation.eeg"]
        seen["names"] = feature["names"]
        seen["unit"] = feature["unit"]
        seen["shape"] = feature["shape"]
        seen["paths"] = paths
        assert "eeg_repair" not in info
        assert "observation.eeg_repair" not in info["features"]
        assert not path.with_name("observation.eeg_repair.parquet").exists()
        assert path.with_name("observation.eeg_repair.parquet.rerun-bak").is_file()

    monkeypatch.setattr("eeg_repair.dataset.repair_dataset", fake_repair)
    assert rerun.rerun_path(path, "uV") == "DONE"
    info = json.loads((tmp_path / "session/meta/info.json").read_text())
    assert seen["names"] == rerun.HW33
    assert seen["names"][0] == "TRIG"
    assert seen["unit"] == "uV"
    assert seen["shape"] == [33]
    assert seen["paths"] == [path.resolve()]
    assert info["features"]["observation.eeg"]["names"] == rerun.HW33
    assert not path.with_name("observation.eeg_repair.parquet").exists()
    assert not list((tmp_path / "session").rglob("*.rerun-bak"))


def test_failed_repair_restores_previous_output_and_keeps_corrected_names(tmp_path, monkeypatch):
    path = _session(tmp_path / "session", rerun.HW33)

    def fail(*args, **kwargs):
        raise RuntimeError("repair failed")

    monkeypatch.setattr("eeg_repair.dataset.repair_dataset", fail)
    try:
        rerun.rerun_path(path, "uV")
    except RuntimeError as exc:
        assert "repair failed" in str(exc)
    else:
        raise AssertionError("expected repair failure")
    info = json.loads((tmp_path / "session/meta/info.json").read_text())
    assert info["features"]["observation.eeg"]["names"] == rerun.HW33
    assert info["features"]["observation.eeg"]["unit"] == "uV"
    assert info["features"]["observation.eeg_repair"]["names"] == ["FP1"]
    assert info["eeg_repair"]["quality"] == "excellent"
    assert path.with_name("observation.eeg_repair.parquet").read_bytes() == b"previous-repair"
    assert not list((tmp_path / "session").rglob("*.rerun-bak"))


def test_dry_run_and_thirty_two_channels_do_not_write(tmp_path):
    path = _session(tmp_path / "session", rerun.HW32, repair=True)
    before = (tmp_path / "session/meta/info.json").read_bytes()
    assert rerun.rerun_path(path, "uV", dry_run=True) == "DRY-RUN"
    assert (tmp_path / "session/meta/info.json").read_bytes() == before
    assert path.with_name("observation.eeg_repair.parquet").read_bytes() == b"previous-repair"


def test_unrenamed_columns_are_rejected(tmp_path):
    path = _session(tmp_path / "session", [f"CH{i:02d}" for i in range(33)], repair=False)
    try:
        rerun.rerun_path(path, "uV", dry_run=True)
    except ValueError as exc:
        assert "hardware order" in str(exc)
    else:
        raise AssertionError("expected hardware-order rejection")
