from dataclasses import replace
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair import algorithms as alg
from eeg_repair.config import Config
from eeg_repair.pipeline import repair_file, impedance_bads, missing_coordinates, prepare_montage

NAMES = ("Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4")


def make_recording(tmp_path):
    rng = np.random.default_rng(17)
    t = np.arange(3000) / 250
    common = 10 * np.sin(2 * np.pi * 10 * t)
    signals = np.stack([common + rng.normal(0, 3, t.size) for _ in NAMES]).astype("float32")
    signals[0] = 0  # Deliberate flat electrode, should be interpolated.
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
    cfg = Config(channels=(*NAMES, "TRIG"), sfreq=250, unit="uV", ica_enabled=False)
    return cfg, path, table


def test_eight_channel_repair_real_algorithms(tmp_path):
    cfg, path, source = make_recording(tmp_path)
    output = tmp_path / "repaired.parquet"
    audit = repair_file(path, output, cfg)
    assert "Fp1" in audit["bad_channels"]
    got = pq.read_table(output)
    assert got["TRIG"].equals(source["TRIG"])
    signal = np.stack([got[n].to_numpy() for n in NAMES])
    assert np.std(signal[0]) > 0
    np.testing.assert_allclose(signal.mean(0), 0, atol=2e-6)
    assert audit["ica"]["skipped"] == "disabled"


def test_ica_executes_and_audits(tmp_path):
    cfg, path, _ = make_recording(tmp_path)
    audit = repair_file(path, tmp_path / "ica.parquet", replace(cfg, ica_enabled=True, ica_components=4))
    assert "skipped" not in audit["ica"], audit["ica"]
    assert audit["ica"]["n_components"] == 4
    assert "Fp1" in audit["ica"]["exclude_channels"]


def test_ica_failure_continues_to_interpolation(tmp_path, monkeypatch):
    cfg, path, _ = make_recording(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("test ICA failure")

    monkeypatch.setattr(alg, "fit_ica", fail)
    result = repair_file(path, tmp_path / "ica.parquet", replace(cfg, ica_enabled=True))
    assert "test ICA failure" in result["ica"]["skipped"]
    assert result["status"] == "SUCCESS"


def test_impedance_threshold_and_units(tmp_path):
    path = tmp_path / "impedance.parquet"
    pq.write_table(pa.table({"Fp1": [149999.0, 150000.0], "Fp2": [1.0, 2.0]}), path)
    cfg = Config(channels=("Fp1", "Fp2"), sfreq=250, unit="uV")
    assert impedance_bads(path, list(cfg.channels), cfg) == ["Fp1"]
    assert impedance_bads(None, list(cfg.channels), cfg) == []
    with pytest.raises(ValueError, match="missing channels"):
        impedance_bads(path, ["C3"], cfg)


def test_missing_coordinates_do_not_stop_processing():
    cfg = Config(channels=("Cz", "Cbz"), sfreq=250, unit="uV", montage="standard_1005")
    montage = prepare_montage(["Cz", "Cbz"], cfg)
    assert missing_coordinates(["Cz", "Cbz"], montage) == ["Cbz"]
    names = ["Fp1", "Fp2", "C3", "C4", "P3", "P4", "O1", "O2", "Cbz"]
    rng = np.random.default_rng(3)
    data = rng.normal(size=(len(names), 1000)).astype(np.float32)
    data[-1] = 40.0
    original = data[-1].copy()
    repaired = alg.interpolate_bads(data, names, 250, ["Fp1", "Cbz"], montage)
    assert np.array_equal(repaired[-1], original)
    assert not np.array_equal(repaired[0], data[0])


def test_dry_run_writes_nothing(tmp_path):
    cfg, path, _ = make_recording(tmp_path)
    output = tmp_path / "new" / "dry.parquet"
    assert repair_file(path, output, cfg, dry_run=True)["status"] == "VALIDATED"
    assert not output.parent.exists()


def test_reference_rule_exemptions():
    assert not alg._is_bad("Fp1", False, True, False, False, False)
    assert alg._is_bad("F7", False, True, False, False, False)
    assert not alg._is_bad("C3", False, False, False, True, False)
    assert alg._is_bad("C3", True, False, False, True, False)
