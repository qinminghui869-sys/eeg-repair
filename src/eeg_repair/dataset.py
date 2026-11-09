"""Publish complete mf-LeRobot datasets with repaired EEG and fresh metadata."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pyarrow.parquet as pq

from .config import load_config
from .pipeline import repair_file


def dataset_root(path):
    return next((p for p in Path(path).resolve().parents if (p / "meta/info.json").is_file()), None)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def audit_relative_path(episode_index: int, *, n_files: int) -> Path:
    """Place observation.eeg.audit.json under meta/; isolate multi-episode files."""
    if n_files == 1:
        return Path("meta/observation.eeg.audit.json")
    return Path(f"meta/episode_{int(episode_index):06d}/observation.eeg.audit.json")


def episode_index(path: Path) -> int:
    episodes = pq.read_table(path, columns=["episode_index"])["episode_index"].unique().to_pylist()
    if len(episodes) != 1:
        raise ValueError("require unique episode_index per EEG file")
    return int(episodes[0])


def repair_dataset(root, destination, paths, config=None, *, dry_run=False, impedance=None):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError("dataset output must be outside source tree")
    if destination.exists():
        raise FileExistsError(f"dataset output already exists: {destination}")
    paths = [Path(p).resolve() for p in paths]
    all_paths = sorted(root.rglob("observation.eeg.parquet"))
    if sorted(paths) != all_paths:
        raise ValueError("dataset mode requires all EEG episodes; pass the dataset root")
    configs = [load_config(config, source=p) for p in paths]
    episodes = [episode_index(path) for path in paths]
    if len(set(episodes)) != len(episodes):
        raise ValueError("require unique episode_index per EEG file")
    audits = [audit_relative_path(index, n_files=len(paths)) for index in episodes]
    # Validate every episode before copying potentially large videos.
    for path, cfg, audit in zip(paths, configs, audits):
        repair_file(
            path,
            destination / path.relative_to(root),
            cfg,
            dry_run=True,
            impedance=impedance,
            audit_path=destination / audit,
        )
    if dry_run:
        return {"status": "VALIDATED", "files": len(paths)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".eeg-dataset-", dir=destination.parent))
    try:
        records = []
        stats = {}
        for path, cfg, index, audit in zip(paths, configs, episodes, audits):
            relative = path.relative_to(root)
            target = stage / relative
            repair_file(path, target, cfg, impedance=impedance, audit_path=stage / audit)
            table = pq.read_table(target)
            values = (
                np.stack([table[n].to_numpy() for n in cfg.channels], axis=1)
                if cfg.layout == "columns"
                else np.asarray(table[cfg.signal_column].to_pylist())
            )
            values = values.astype(np.float64)
            stats[index] = {
                key: getattr(values, key)(axis=0).tolist() for key in ("min", "max", "mean", "std")
            }
            stats[index]["count"] = [len(values)]
            records.append(
                {
                    "episode_index": index,
                    "data": relative.as_posix(),
                    "audit": audit.as_posix(),
                }
            )
        # Copies, never hardlinks: later changes to output cannot alter the source.
        for source in root.rglob("*"):
            relative = source.relative_to(root)
            target = stage / relative
            if source.is_symlink():
                raise ValueError(f"dataset symlinks are unsupported: {relative}")
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file() and source not in paths:
                if target.exists():
                    raise ValueError(f"source conflicts with generated output: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        info_path = stage / "meta/info.json"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        feature = info["features"]["observation.eeg"]
        feature["unit"] = configs[0].unit
        # Existing names, shape, dtype, fps, paths and time-alignment fields stay intact.
        info["eeg_repair"] = {
            "version": 1,
            "status": "complete",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "feature": "observation.eeg",
            "records": records,
        }
        write_json(info_path, info)
        stats_path = stage / "meta/episodes_stats.jsonl"
        rows = (
            [json.loads(line) for line in stats_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if stats_path.exists()
            else []
        )
        seen = set()
        for row in rows:
            episode = row["episode_index"]
            if episode in stats:
                if episode in seen:
                    raise ValueError("duplicate episode in episodes_stats.jsonl")
                row.setdefault("stats", {})["observation.eeg"] = stats[episode]
                seen.add(episode)
        rows.extend(
            {"episode_index": e, "stats": {"observation.eeg": s}} for e, s in stats.items() if e not in seen
        )
        stats_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        # Legacy global statistics cannot describe repaired signals.
        global_stats = stage / "meta/stats.json"
        if global_stats.exists():
            raise ValueError("legacy meta/stats.json is unsupported; refusing stale global EEG statistics")
        if destination.exists():
            raise FileExistsError(destination)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"status": "SUCCESS", "files": len(paths)}
