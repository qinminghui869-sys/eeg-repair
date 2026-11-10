"""Add repaired EEG beside the source Parquet and update dataset metadata in place."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

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
    relatives = [repaired_relative(path.relative_to(root)) for path in paths]
    finals = [root / relative for relative in relatives]
    for final in finals:
        if final.exists():
            raise FileExistsError(f"repair output already exists: {final}")
    if (root / "meta/stats.json").exists():
        raise ValueError("legacy meta/stats.json is unsupported; refusing stale global EEG statistics")
    for path, cfg, final in zip(paths, configs, finals):
        repair_file(path, final, cfg, dry_run=True, impedance=impedance)
    if dry_run:
        return {"status": "VALIDATED", "files": len(paths)}
    stage = Path(tempfile.mkdtemp(prefix=".eeg-dataset-", dir=root))
    published = []
    info_path = root / "meta/info.json"
    info_backup = info_path.read_bytes()
    try:
        records = []
        staged = []
        for path, cfg, index, relative, final in zip(paths, configs, episodes, relatives, finals):
            target = stage / relative
            repair_file(path, target, cfg, impedance=impedance)
            records.append({"episode_index": index, "data": relative.as_posix()})
            staged.append((target, final))
        for source, final in staged:
            final.parent.mkdir(parents=True, exist_ok=True)
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
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        if info_path.exists():
            info_path.write_bytes(info_backup)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"status": "SUCCESS", "files": len(paths)}
