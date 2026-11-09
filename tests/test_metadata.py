import json
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair.config import load_config
from eeg_repair.metadata import read_metadata


def make_dataset(root, names, sfreq, unit=None):
    (root / "meta").mkdir(parents=True)
    feature = dict(names=names, fps=sfreq, shape=[len(names)])
    if unit:
        feature["unit"] = unit
    (root / "meta/info.json").write_text(json.dumps({"features": {"observation.eeg": feature}}))
    (root / "meta/collect_info.jsonl").write_text(
        json.dumps({"episode_index": 7, "hardware": {"eeg": "test device"}}) + "\n"
    )
    path = root / "observation.eeg.parquet"
    pq.write_table(pa.table({"episode_index": [7, 7], **{n: [1.0, 2.0] for n in names}}), path)
    return path


def test_per_file_parameters_and_device(tmp_path):
    for i, rate in enumerate([250.0, 1000.0]):
        path = make_dataset(tmp_path / str(i), ["Fp1", "Fp2", "C3", "C4"], rate, "uV")
        cfg = load_config(source=path)
        assert cfg.sfreq == rate
        assert cfg.device == "test device"
        assert cfg.channels == ("Fp1", "Fp2", "C3", "C4")


def test_missing_unit_and_metadata_conflict(tmp_path):
    path = make_dataset(tmp_path / "data", ["Fp1"], 250.0)
    with pytest.raises(ValueError, match="unit"):
        load_config(source=path)
    config = tmp_path / "config.yaml"
    config.write_text("unit: uV\n")
    assert load_config(config, source=path).unit == "uV"
    config.write_text("unit: uV\nsfreq: 1000\n")
    with pytest.raises(ValueError, match="conflicts"):
        load_config(config, source=path)


_REAL_ROOT_ENV = os.environ.get("EEG_TEST_DATA")
REAL_ROOT = Path(_REAL_ROOT_ENV) if _REAL_ROOT_ENV else Path()
REAL_FILES = sorted(REAL_ROOT.rglob("observation.eeg.parquet")) if REAL_ROOT.exists() else []


@pytest.mark.skipif(not REAL_FILES, reason="external EEG dataset unavailable")
@pytest.mark.parametrize("path", REAL_FILES, ids=lambda p: p.parents[3].name)
def test_real_recording_contract(path):
    metadata = read_metadata(path)
    table = pq.read_table(path)
    assert metadata["shape"] == [len(metadata["channels"])]
    assert metadata["device"]
    assert all(n in table.column_names for n in metadata["channels"])
    stamps = table["timestamp"].to_numpy()
    assert np.isfinite(stamps).all() and np.all(np.diff(stamps) > 0)
    assert abs(1 / np.median(np.diff(stamps)) / metadata["sfreq"] - 1) < 0.005
    for name in metadata["channels"]:
        assert np.isfinite(table[name].to_numpy()).all()
    if metadata["unit"] is None:
        with pytest.raises(ValueError, match="unit"):
            load_config(source=path)
