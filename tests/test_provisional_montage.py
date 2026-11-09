"""Provisional assets must be complete, traceable, and explicitly selected."""

from dataclasses import replace
from pathlib import Path
import importlib.util
import hashlib
import json

import numpy as np
import pytest
import yaml

from eeg_repair import algorithms as alg
from eeg_repair.config import load_config
from eeg_repair.pipeline import prepare_montage, montage_evidence

ROOT = Path(__file__).resolve().parents[1]


def config_for_device(name):
    device = yaml.safe_load((ROOT / "configs" / name).read_text())
    return load_config_from_values(device)


def load_config_from_values(device):
    from eeg_repair.config import Config

    return Config(
        channels=tuple(device["channels"]),
        sfreq=device["sfreq"],
        unit="uV",
        montage_csv=str(ROOT / "configs/montages/provisional_full.csv"),
        montage_report=str(ROOT / "configs/montages/provisional_full.registration.json"),
    )


@pytest.mark.parametrize("device", ["neuroscan_128.yaml", "brainco_31.yaml"])
def test_full_device_coverage_and_evidence(device):
    cfg = config_for_device(device)
    _, names = alg.scalp_names(list(cfg.channels))
    montage = prepare_montage(names, cfg)
    assert montage.get_positions()["coord_frame"] == "head"
    evidence = montage_evidence(cfg)
    assert evidence["registration"]["status"] == "provisional_unvalidated_128"
    assert len(names) == (128 if device.startswith("neuroscan") else 31)
    pos = alg._montage_pos(montage)
    assert pos["F11"][0] < 0 < pos["F12"][0]
    assert all(np.linalg.norm(pos[n.upper()]) < 0.25 for n in names)


def test_changed_csv_rejected(tmp_path):
    cfg = config_for_device("neuroscan_128.yaml")
    altered = tmp_path / "altered.csv"
    altered.write_bytes(Path(cfg.montage_csv).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash"):
        prepare_montage(["F11"], replace(cfg, montage_csv=str(altered)))


def test_report_frame_rejected(tmp_path):
    cfg = config_for_device("neuroscan_128.yaml")
    report = json.loads(Path(cfg.montage_report).read_text())
    report["coord_frame"] = "mri"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="head coordinates"):
        prepare_montage(["F11"], replace(cfg, montage_report=str(path)))


def test_similarity_recovers_known_transform_and_reproduces_asset(tmp_path):
    spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build_provisional_montage.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    x = np.random.default_rng(7).normal(size=(20, 3))
    rotation = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    y = 1.2 * x @ rotation.T + np.array([0.1, -0.2, 0.3])
    fit = builder.fit_similarity(x, y)
    np.testing.assert_allclose(builder.apply(x, fit), y, atol=1e-12)
    assert np.linalg.det(fit[1]) > 0
    assets = ROOT / "configs/montages"
    (tmp_path / "neuroscan_net32_pan_mm.csv").write_bytes(
        (assets / "neuroscan_net32_pan_mm.csv").read_bytes()
    )
    builder.ASSETS = tmp_path
    builder.build()
    generated = (tmp_path / "provisional_full.csv").read_bytes()
    assert generated == (assets / "provisional_full.csv").read_bytes()
    assert (
        hashlib.sha256(generated).hexdigest()
        == json.loads((tmp_path / "provisional_full.registration.json").read_text())["csv_sha256"]
    )


def test_report_path_is_relative_to_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "channels: [F11]\nsfreq: 1000\nunit: uV\nmontage_csv: positions.csv\nmontage_report: report.json\n"
    )
    cfg = load_config(path)
    assert cfg.montage_report == str(tmp_path / "report.json")
