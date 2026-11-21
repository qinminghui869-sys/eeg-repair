"""Offline base + overlay merge; produces template estimates, not measured positions."""

import csv
import hashlib
import json
from pathlib import Path

import mne
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "configs/montages"


def fit_similarity(source, target):
    """Column-vector transform y = scale * rotation @ x + translation, no reflection."""
    x, y = source - source.mean(0), target - target.mean(0)
    u, singular, vt = np.linalg.svd(x.T @ y)
    signs = np.ones(3)
    signs[-1] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ np.diag(signs) @ u.T
    scale = (singular * signs).sum() / (x * x).sum()
    translation = target.mean(0) - scale * rotation @ source.mean(0)
    return scale, rotation, translation


def apply(points, fit):
    scale, rotation, translation = fit
    return scale * points @ rotation.T + translation


def build():
    source_path = ASSETS / "neuroscan_net32_pan_mm.csv"
    with source_path.open() as stream:
        rows = (line for line in stream if line.strip() and not line.lstrip().startswith("#"))
        source = {
            r["name"]: np.array([float(r[k]) for k in ("x_mm", "y_mm", "z_mm")]) / 1000
            for r in csv.DictReader(rows)
        }
    # colin27_1005 is the renamed standard_1005 in the locked MNE version.
    base = mne.channels.make_standard_montage("colin27_1005")
    base.apply_trans(mne.channels.compute_native_head_t(base))
    assert base.get_positions()["coord_frame"] == "head"
    positions = base.get_positions()["ch_pos"]
    # Mastoid sites differ between cap templates; exclude them from registration.
    common = sorted(set(source) & set(positions) - {"M1", "M2"})
    x, y = np.array([source[n] for n in common]), np.array([positions[n] for n in common])
    fit = fit_similarity(x, y)
    residuals = np.linalg.norm(apply(x, fit) - y, axis=1) * 1000
    loo = []
    for i in range(len(common)):
        keep = np.arange(len(common)) != i
        loo.append(
            float(np.linalg.norm(apply(x[i : i + 1], fit_similarity(x[keep], y[keep]))[0] - y[i]) * 1000)
        )
    # Published CURRY PAN xyz (mm), converted to meters. Not passed through the similarity transform.
    published = ("F11", "F12", "FT11", "FT12")
    overlay = {n: source[n].copy() for n in published}
    aliases = {"Cb1": "I1", "Cb2": "I2", "Cbz": "Iz"}
    overlay.update({n: positions[target].copy() for n, target in aliases.items()})
    positions.update(overlay)
    names = []
    channels = yaml.safe_load((ROOT / "configs" / "neuroscan_128.yaml").read_text())["channels"]
    for n in channels:
        if n.upper() not in {"VEOG", "HEOG", "EKG", "EMG"} and n.upper() not in {k.upper() for k in names}:
            names.append(n)
    lookup = {n.upper(): p for n, p in positions.items()}
    coords = np.array([lookup[n.upper()] for n in names])
    if not np.isfinite(coords).all() or len(np.unique(coords, axis=0)) != len(coords):
        raise ValueError("Invalid or duplicate coordinates")
    for left, right in [("F11", "F12"), ("FT11", "FT12"), ("Cb1", "Cb2")]:
        if not overlay[left][0] < 0 < overlay[right][0]:
            raise ValueError("Left/right orientation invalid")
    output = ASSETS / "provisional_full.csv"
    with output.open("w", newline="") as stream:
        stream.write(
            "# 试运行坐标，单位米。F11/F12/FT11/FT12 为 PAN 换算值，不在 head 坐标系；其余为 MNE head。\n"
        )
        writer = csv.writer(stream)
        writer.writerow(["name", "x_m_mne", "y_m_mne", "z_m_mne"])
        writer.writerows([n, *[f"{v:.12f}" for v in lookup[n.upper()]]] for n in names)
    matrix = np.eye(4)
    matrix[:3, :3] = fit[0] * fit[1]
    matrix[:3, 3] = fit[2]
    report = {
        "description": "试运行坐标的生成记录。运行时校验 csv_sha256，不把本文件当作电极坐标。",
        "status": "provisional_unvalidated_128",
        "coord_frame": "head",
        "unit": "m",
        "base": "colin27_1005 (formerly standard_1005)",
        "mne_version": mne.__version__,
        "source_url": "https://sccn.ucsd.edu/pipermail/eeglablist/2022/016586.html",
        "alias_source_url": "https://www.compumedicsneuroscan.com/Uploads/Curry7012ReleaseNotes.pdf#page=13",
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "csv_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "common_channels": common,
        "excluded_registration_channels": ["M1", "M2"],
        "pan_m_to_head_m": matrix.tolist(),
        "scale": float(fit[0]),
        "residual_mm": dict(zip(common, residuals.tolist())),
        "rmse_mm": float(np.sqrt(np.mean(residuals**2))),
        "leave_one_out_mm": dict(zip(common, loo)),
        "leave_one_out_rmse_mm": float(np.sqrt(np.mean(np.array(loo) ** 2))),
        "aliases": aliases,
        "published_pan_m": {n: overlay[n].tolist() for n in published},
        "published_pan_source": "CURRY default PAN xyz from https://sccn.ucsd.edu/pipermail/eeglablist/2022/016586.html, millimeters converted to meters, not similarity-transformed.",
        "overlay_head_m": {n: overlay[n].tolist() for n in aliases},
        "channels": len(names),
        "limitation": "F11/F12/FT11/FT12 keep the published PAN coordinates in meters. Cb1/Cb2/Cbz are template aliases. Neither set is a measured 128-cap position. Registration residuals do not describe these seven sites.",
    }
    (ASSETS / "provisional_full.registration.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("channels", "scale", "rmse_mm", "leave_one_out_rmse_mm")}, indent=2
        )
    )


if __name__ == "__main__":
    build()
