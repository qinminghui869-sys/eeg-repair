"""Filter → impedance → five-rule QC → ICA → spline → average reference."""

from pathlib import Path
from datetime import datetime, timezone
import importlib.metadata
import json
import hashlib

import mne
import numpy as np
import pyarrow.parquet as pq

from . import algorithms as alg
from .config import Config
from .parquet import publish, read_recording, replace_signals


def montage_evidence(cfg: Config) -> dict:
    if not cfg.montage_csv:
        return {"kind": "standard_template", "name": cfg.montage}
    digest = hashlib.sha256(Path(cfg.montage_csv).read_bytes()).hexdigest()
    evidence = {"kind": "custom_csv", "csv_sha256": digest}
    if cfg.montage_report:
        report = json.loads(Path(cfg.montage_report).read_text(encoding="utf-8"))
        if report.get("csv_sha256") != digest:
            raise ValueError("montage CSV hash does not match registration report")
        if report.get("coord_frame") != "head" or report.get("unit") != "m":
            raise ValueError("montage report must specify head coordinates in meters")
        evidence["registration"] = report
    return evidence


def prepare_montage(names: list[str], cfg: Config):
    """Attach available coordinates. Missing names do not stop processing."""
    montage_evidence(cfg)
    montage = (
        alg.load_montage(cfg.montage_csv)
        if cfg.montage_csv
        else mne.channels.make_standard_montage(cfg.montage)
    )
    positions = alg._montage_pos(montage)
    present = [
        name for name in names if name.upper() in positions and np.isfinite(positions[name.upper()]).all()
    ]
    if len({tuple(positions[name.upper()]) for name in present}) != len(present):
        raise ValueError("electrode positions must be distinct")
    return montage


def missing_coordinates(names: list[str], montage) -> list[str]:
    positions = alg._montage_pos(montage)
    return [
        name
        for name in names
        if name.upper() not in positions or not np.isfinite(positions[name.upper()]).all()
    ]


def impedance_bads(path: Path | None, names: list[str], cfg: Config) -> list[str]:
    if path is None:
        return []
    table = pq.read_table(path)
    if not table.num_rows:
        raise ValueError("empty impedance file")
    missing = set(names) - set(table.column_names)
    if missing:
        raise ValueError(f"impedance file missing channels: {sorted(missing)}")
    bad = []
    for name in names:
        values = np.asarray(table[name].to_numpy(), dtype=float)
        if not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError(f"invalid impedance values: {name}")
        if cfg.impedance_unit == "ohm":
            values = values / 1000.0
        if np.any(values >= cfg.impedance_threshold_kohm):
            bad.append(name)
    return bad


def filter_file(
    source: str | Path,
    destination: str | Path,
    cfg: Config,
    *,
    dry_run: bool = False,
) -> dict:
    """Bandpass and notch scalp channels. Do not remove DC or run repair steps."""
    record = read_recording(source, cfg)
    destination = Path(destination)
    scalp, names = alg.scalp_names(list(cfg.channels))
    if not names:
        raise ValueError("no scalp channels to filter")
    if destination.resolve() == record.path or destination.exists():
        raise ValueError("destination must be new and distinct from source")
    if dry_run:
        return {
            "status": "VALIDATED",
            "source": str(record.path),
            "scalp_channels": len(names),
            "samples": record.table.num_rows,
        }
    clean = alg.bandpass_notch(
        record.data_uv[scalp],
        names,
        cfg.sfreq,
        l_freq=cfg.highpass_hz,
        h_freq=cfg.lowpass_hz,
        notch_hz=cfg.notch_hz,
    )
    table = replace_signals(record, clean, cfg, scalp)
    publish(record, table, destination)
    return {
        "status": "SUCCESS",
        "source": str(record.path),
        "scalp_channels": len(names),
        "samples": record.table.num_rows,
    }


def repair_file(
    source: str | Path,
    destination: str | Path,
    cfg: Config,
    *,
    impedance: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    record = read_recording(source, cfg)
    destination = Path(destination)
    scalp, names = alg.scalp_names(list(cfg.channels))
    if len(names) < 4:
        raise ValueError("require at least four scalp electrodes")
    evidence = montage_evidence(cfg)
    montage = prepare_montage(names, cfg)
    evidence = {**evidence, "missing_coordinates": missing_coordinates(names, montage)}
    if impedance is None:
        candidate = record.path.with_name("observation.eeg_impedance.parquet")
        impedance = candidate if candidate.exists() else None
    prior = impedance_bads(Path(impedance) if impedance is not None else None, names, cfg)
    if destination.resolve() == record.path or destination.exists():
        raise ValueError("destination must be new and distinct from source")
    if dry_run:
        return {
            "status": "VALIDATED",
            "montage": evidence,
            "source": str(record.path),
            "scalp_channels": len(names),
            "samples": record.table.num_rows,
            "impedance_bad_channels": prior,
        }
    centered = alg.remove_dc(record.data_uv[scalp])

    def filtered(low):
        return alg.bandpass_notch(
            centered, names, cfg.sfreq, l_freq=low, h_freq=cfg.lowpass_hz, notch_hz=cfg.notch_hz
        )

    clean = filtered(cfg.highpass_hz)
    qc = alg.mark_bad_channels(clean, names, cfg.sfreq, montage)
    bads = [n for n in names if n in set(qc["bad_channels"]) | set(prior)]
    rate = len(bads) / len(names)
    if rate > cfg.max_bad_ratio or len(names) - len(bads) < 4:
        raise ValueError(
            f"insufficient good channels: {len(bads)}/{len(names)} bad; max_bad_ratio={cfg.max_bad_ratio}"
        )
    ica_info = {"excluded": [], "skipped": "disabled"}
    if cfg.ica_enabled:
        if clean.shape[1] / cfg.sfreq < 5:
            ica_info = {"excluded": [], "skipped": "less than 5 seconds"}
        else:
            try:
                fit_data = filtered(max(1.0, cfg.highpass_hz))
                ica, fit_bads, ica_info = alg.fit_ica(
                    [fit_data], names, cfg.sfreq, bads, montage, n_components=cfg.ica_components
                )
                clean = alg.apply_ica(clean, names, cfg.sfreq, ica, fit_bads, montage)
                share = len(ica_info["muscle_significant"]) / max(ica_info["n_components"], 1)
                ica_info["muscle_warning"] = (
                    "severe" if share >= 0.9 else ("warning" if share >= 0.8 else None)
                )
                ica_info["fit_highpass_hz"] = max(1.0, cfg.highpass_hz)
            except Exception as exc:
                ica_info = {"excluded": [], "skipped": f"{type(exc).__name__}: {exc}"}
    clean = alg.interpolate_bads(clean, names, cfg.sfreq, bads, montage)
    clean = alg.average_reference(clean, names, cfg.sfreq, montage)
    table = replace_signals(record, clean, cfg, scalp)
    audit = {
        "status": "SUCCESS",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": cfg.to_dict(),
        "montage": evidence,
        "qc": qc,
        "impedance_bad_channels": prior,
        "bad_channels": bads,
        "bad_rate": rate,
        "n_bad": len(bads),
        "n_scalp": len(names),
        "quality": alg.quality_from_bad_rate(rate),
        "qc_passed": rate < alg.PASS_BAD_RATE,
        "ica": ica_info,
        "reference": "all_scalp_after_interpolation",
        "versions": {name: importlib.metadata.version(name) for name in ("mne", "numpy", "scipy", "pyarrow")},
    }
    publish(record, table, destination)
    return audit
