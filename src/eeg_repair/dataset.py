"""Add repaired EEG beside the source Parquet and update dataset metadata in place."""

from datetime import datetime, timezone
import json
import os
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".eeg-meta-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def audit_relative_path(episode_index: int, *, n_files: int) -> Path:
    """Place observation.eeg.audit.json under meta/; isolate multi-episode files."""
    if n_files == 1:
        return Path("meta/observation.eeg.audit.json")
    return Path(f"meta/episode_{int(episode_index):06d}/observation.eeg.audit.json")


REPAIR_NAME = "observation.eeg_repair.parquet"
REPAIR_FEATURE = "observation.eeg_repair"


def repaired_relative(source_relative: Path) -> Path:
    return source_relative.with_name(REPAIR_NAME)


def episode_index(path: Path) -> int:
    episodes = pq.read_table(path, columns=["episode_index"])["episode_index"].unique().to_pylist()
    if len(episodes) != 1:
        raise ValueError("require unique episode_index per EEG file")
    return int(episodes[0])


def repair_dataset(root, paths, config=None, *, dry_run=False, impedance=None):
    """Write observation.eeg_repair.parquet next to each source file. Never overwrite observation.eeg.parquet."""
    root = Path(root).resolve()
    paths = [Path(p).resolve() for p in paths]
    all_paths = sorted(p for p in root.rglob("observation.eeg.parquet") if ".eeg-dataset-" not in p.parts)
    if sorted(paths) != all_paths:
        raise ValueError("dataset mode requires all EEG episodes; pass the dataset root")
    configs = [load_config(config, source=p) for p in paths]
    episodes = [episode_index(path) for path in paths]
    if len(set(episodes)) != len(episodes):
        raise ValueError("require unique episode_index per EEG file")
    audits = [audit_relative_path(index, n_files=len(paths)) for index in episodes]
    relatives = [repaired_relative(path.relative_to(root)) for path in paths]
    finals = [root / relative for relative in relatives]
    audit_paths = [root / audit for audit in audits]
    for final, audit_path in zip(finals, audit_paths):
        if final.exists() or audit_path.exists():
            raise FileExistsError(f"repair output already exists: {final}")
    if (root / "meta/stats.json").exists():
        raise ValueError("legacy meta/stats.json is unsupported; refusing stale global EEG statistics")
    for path, cfg, final, audit_path in zip(paths, configs, finals, audit_paths):
        repair_file(path, final, cfg, dry_run=True, impedance=impedance, audit_path=audit_path)
    if dry_run:
        return {"status": "VALIDATED", "files": len(paths)}
    stage = Path(tempfile.mkdtemp(prefix=".eeg-dataset-", dir=root))
    published = []
    info_path = root / "meta/info.json"
    stats_path = root / "meta/episodes_stats.jsonl"
    info_backup = info_path.read_bytes()
    stats_backup = stats_path.read_bytes() if stats_path.exists() else None
    try:
        records = []
        computed = {}
        staged = []
        for path, cfg, index, audit, relative, final, audit_path in zip(
            paths, configs, episodes, audits, relatives, finals, audit_paths
        ):
            target = stage / relative
            repair_file(path, target, cfg, impedance=impedance, audit_path=stage / audit)
            table = pq.read_table(target)
            values = (
                np.stack([table[n].to_numpy() for n in cfg.channels], axis=1)
                if cfg.layout == "columns"
                else np.asarray(table[cfg.signal_column].to_pylist())
            )
            values = values.astype(np.float64)
            computed[index] = {
                key: getattr(values, key)(axis=0).tolist() for key in ("min", "max", "mean", "std")
            }
            computed[index]["count"] = [len(values)]
            records.append({"episode_index": index, "data": relative.as_posix(), "audit": audit.as_posix()})
            staged.append((target, final, stage / audit, audit_path))
        for source, final, audit_source, audit_final in staged:
            final.parent.mkdir(parents=True, exist_ok=True)
            audit_final.parent.mkdir(parents=True, exist_ok=True)
            os.link(audit_source, audit_final)
            published.append(audit_final)
            os.link(source, final)
            published.append(final)
        info = json.loads(info_backup)
        source_feature = info["features"]["observation.eeg"]
        if REPAIR_FEATURE in info["features"]:
            raise ValueError("observation.eeg_repair already present in meta/info.json")
        repaired_feature = json.loads(json.dumps(source_feature))
        repaired_feature["unit"] = configs[0].unit
        info["features"][REPAIR_FEATURE] = repaired_feature
        info["eeg_repair"] = {
            "version": 1,
            "status": "complete",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "feature": REPAIR_FEATURE,
            "source_feature": "observation.eeg",
            "records": records,
        }
        write_json(info_path, info)
        rows = (
            [json.loads(line) for line in stats_backup.decode().splitlines() if line.strip()]
            if stats_backup is not None
            else []
        )
        seen = set()
        for row in rows:
            episode = row["episode_index"]
            if episode in computed:
                if episode in seen:
                    raise ValueError("duplicate episode in episodes_stats.jsonl")
                row.setdefault("stats", {})[REPAIR_FEATURE] = computed[episode]
                seen.add(episode)
        rows.extend(
            {"episode_index": e, "stats": {REPAIR_FEATURE: s}} for e, s in computed.items() if e not in seen
        )
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".eeg-meta-", dir=stats_path.parent)
        os.close(fd)
        temporary = Path(name)
        temporary.write_text(
            "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        os.replace(temporary, stats_path)
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        if info_path.exists():
            info_path.write_bytes(info_backup)
        if stats_backup is None:
            stats_path.unlink(missing_ok=True)
        else:
            stats_path.write_bytes(stats_backup)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"status": "SUCCESS", "files": len(paths)}
