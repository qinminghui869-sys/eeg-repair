import json
import os
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair.quality import check_recording, main

_REAL_ROOT_ENV = os.environ.get("EEG_TEST_DATA")
REAL_ROOT = Path(_REAL_ROOT_ENV) if _REAL_ROOT_ENV else Path()
REAL_FILES = (
    sorted(REAL_ROOT.glob("*/data/chunk-000/episode_000000/observation.eeg.parquet"))
    if REAL_ROOT.exists()
    else []
)


def write_eeg(path: Path, stamps, signals, *, names=("Fp1", "Fp2")):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {
        "timestamp": np.asarray(stamps, dtype=np.float64),
        "episode_index": np.zeros(len(stamps), dtype=np.int64),
    }
    for name, values in zip(names, signals):
        columns[name] = np.asarray(values, dtype=np.float32)
    pq.write_table(pa.table(columns), path)


def test_complete_recording_passes(tmp_path):
    stamps = np.arange(250) / 250
    write_eeg(tmp_path / "observation.eeg_repair.parquet", stamps, [np.zeros(250), np.ones(250)])
    report = check_recording(tmp_path / "observation.eeg_repair.parquet")
    assert report["status"] == "PASS"
    assert report["rows"] == 250
    assert report["null_count"] == 0
    assert report["missing_frames"] == 0


def test_null_and_non_finite_values_fail(tmp_path):
    stamps = np.arange(8) / 250
    signal = np.array([1, 2, np.nan, 4, 5, 6, 7, 8], dtype=np.float32)
    table = pa.table(
        {
            "timestamp": stamps,
            "episode_index": np.zeros(len(stamps), dtype=np.int64),
            "Fp1": pa.array([1, None, 3, 4, 5, 6, 7, 8], type=pa.float32()),
            "Fp2": signal,
        }
    )
    path = tmp_path / "observation.eeg_repair.parquet"
    pq.write_table(table, path)
    report = check_recording(path)
    codes = {item["code"] for item in report["issues"]}
    assert report["status"] == "FAIL"
    assert "null" in codes
    assert "non_finite" in codes
    assert report["null_count"] == 1


def test_timestamp_gap_counts_missing_frames(tmp_path):
    stamps = np.array([0, 0.004, 0.008, 0.024, 0.028])
    write_eeg(tmp_path / "observation.eeg_repair.parquet", stamps, [np.zeros(5), np.zeros(5)])
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "info.json").write_text(
        json.dumps({"features": {"observation.eeg": {"names": ["Fp1", "Fp2"], "fps": 250.0, "shape": [2]}}})
    )
    report = check_recording(tmp_path / "observation.eeg_repair.parquet")
    assert report["status"] == "FAIL"
    assert report["missing_frames"] == 3
    assert any(item["code"] == "missing_frames" for item in report["issues"])


def test_empty_file_fails(tmp_path):
    path = tmp_path / "observation.eeg_repair.parquet"
    pq.write_table(
        pa.table(
            {
                "timestamp": pa.array([], type=pa.float64()),
                "episode_index": pa.array([], type=pa.int64()),
                "Fp1": pa.array([], type=pa.float32()),
            }
        ),
        path,
    )
    report = check_recording(path)
    assert report["status"] == "FAIL"
    assert report["issues"][0]["code"] == "empty"


def test_command_rejects_missing_input(tmp_path, capsys):
    assert main([str(tmp_path / "absent.parquet")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "FAIL"


@pytest.mark.skipif(not REAL_FILES, reason="external EEG dataset unavailable")
@pytest.mark.parametrize("path", REAL_FILES, ids=lambda path: path.parents[3].name)
def test_l3_recordings_have_no_nulls_or_missing_frames(path):
    report = check_recording(path)
    assert report["issues"] == []
    assert report["status"] == "PASS"
    assert report["rows"] > 0
    assert report["null_count"] == 0
    assert report["missing_frames"] == 0


@pytest.mark.skipif(not REAL_FILES, reason="external EEG dataset unavailable")
def test_l3_directory_command(capsys):
    assert main([str(REAL_ROOT), "--pattern", "observation.eeg.parquet"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["failed"] == 0
    assert report["files"] == len(REAL_FILES)
    found = {Path(item["path"]).resolve() for item in report["results"]}
    assert found == {path.resolve() for path in REAL_FILES}
