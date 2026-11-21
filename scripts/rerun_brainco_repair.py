#!/usr/bin/env python3
"""用已改成硬件列名的 BrainCo Parquet 重跑修复，并覆盖旧的 observation.eeg_repair.parquet。

先把 meta/info.json 里 observation.eeg 的 names、shape 改成与 Parquet 列一致。
单位为空时写入 --unit。然后移开已有修复文件，重新修复；失败时恢复旧修复文件。

用法:
  python scripts/rerun_brainco_repair.py --index eeg_device_channel_index.json --unit uV --jobs 8
  python scripts/rerun_brainco_repair.py --index eeg_device_channel_index.json --unit uV --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


def _load_fix():
    path = Path(__file__).resolve().parent / "fix_brainco_parquet_channel_names.py"
    spec = importlib.util.spec_from_file_location("fix_brainco_parquet_channel_names", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FIX = _load_fix()
HW32 = list(_FIX.HW32)
HW33 = list(_FIX.HW33)
META = set(_FIX.META)
REPAIR_NAME = "observation.eeg_repair.parquet"
REPAIR_FEATURE = "observation.eeg_repair"


def dataset_root(path: Path) -> Path | None:
    return next((parent for parent in path.resolve().parents if (parent / "meta/info.json").is_file()), None)


def hardware_channels(column_names: list[str]) -> list[str]:
    channels = [name for name in column_names if name not in META]
    if channels == HW32 or channels == HW33:
        return channels
    raise ValueError(f"parquet channels are not BrainCo hardware order ({len(channels)} columns)")


def apply_channel_names(info: dict, channels: list[str], unit: str | None) -> None:
    feature = info["features"]["observation.eeg"]
    declared = feature.get("unit")
    if declared in (None, "", "null"):
        if not unit:
            raise ValueError("metadata unit is empty; pass --unit")
        feature["unit"] = unit
    elif unit and declared != unit:
        raise ValueError(f"metadata unit {declared} conflicts with --unit {unit}")
    feature["names"] = list(channels)
    feature["shape"] = [len(channels)]
    info["features"].pop(REPAIR_FEATURE, None)
    info.pop("eeg_repair", None)


def _repair_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(REPAIR_NAME)
        if ".eeg-dataset-" not in path.parts and not path.name.endswith(".rerun-bak")
    )


def _move_repairs(root: Path) -> list[tuple[Path, Path]]:
    moved = []
    for path in _repair_files(root):
        backup = path.with_name(path.name + ".rerun-bak")
        if backup.exists():
            raise FileExistsError(f"stale rerun backup: {backup}")
        path.replace(backup)
        moved.append((backup, path))
    return moved


def _restore_repairs(moved: list[tuple[Path, Path]]) -> None:
    for backup, dest in moved:
        if dest.exists():
            dest.unlink()
        if backup.exists():
            backup.replace(dest)


def _discard_repairs(moved: list[tuple[Path, Path]]) -> None:
    for backup, _dest in moved:
        backup.unlink(missing_ok=True)


def _write_json(path: Path, value: dict) -> None:
    from eeg_repair.dataset import write_json

    write_json(path, value)


def rerun_path(parquet: Path, unit: str | None, *, dry_run: bool = False) -> str:
    parquet = Path(parquet)
    if not parquet.is_file():
        raise FileNotFoundError(parquet)
    root = dataset_root(parquet)
    if root is None:
        raise FileNotFoundError(f"meta/info.json not found for {parquet}")
    import pyarrow.parquet as pq

    channels = hardware_channels(pq.read_schema(parquet).names)
    info_path = root / "meta/info.json"
    original = json.loads(info_path.read_text(encoding="utf-8"))
    corrected = json.loads(info_path.read_text(encoding="utf-8"))
    apply_channel_names(corrected, channels, unit)
    paths = sorted(
        path for path in root.rglob("observation.eeg.parquet") if ".eeg-dataset-" not in path.parts
    )
    if parquet.resolve() not in {path.resolve() for path in paths}:
        raise ValueError(f"{parquet} is not an EEG file of {root}")
    if dry_run:
        return "DRY-RUN"
    _write_json(info_path, corrected)
    moved = _move_repairs(root)
    try:
        from eeg_repair.dataset import repair_dataset

        repair_dataset(root, paths)
    except BaseException:
        _restore_repairs(moved)
        current = json.loads(info_path.read_text(encoding="utf-8"))
        current["features"]["observation.eeg"] = corrected["features"]["observation.eeg"]
        old_features = original.get("features") or {}
        if REPAIR_FEATURE in old_features:
            current["features"][REPAIR_FEATURE] = old_features[REPAIR_FEATURE]
        else:
            current["features"].pop(REPAIR_FEATURE, None)
        if "eeg_repair" in original:
            current["eeg_repair"] = original["eeg_repair"]
        else:
            current.pop("eeg_repair", None)
        _write_json(info_path, current)
        raise
    _discard_repairs(moved)
    return "DONE"


def _brainco_paths(index: dict) -> list[str]:
    device = index.get("by_device", {}).get("BrainCo BCIGo")
    if not device:
        raise ValueError("index has no by_device.BrainCo BCIGo")
    paths = []
    for block in device.values():
        for scene in block.get("by_scene", {}).values():
            paths.extend(scene.get("paths") or [])
    return paths


def _tasks(paths: list[str]) -> list[str]:
    """One parquet per dataset. A missing file is kept so the run reports it."""
    chosen = {}
    for raw in paths:
        path = Path(raw)
        root = dataset_root(path) if path.is_file() else None
        key = str(root) if root is not None else raw
        chosen.setdefault(key, raw)
    return list(chosen.values())


def worker(task: tuple[str, str | None, bool]) -> tuple[str, str, str]:
    parquet, unit, dry_run = task
    for key, value in (
        ("OMP_NUM_THREADS", "1"),
        ("OPENBLAS_NUM_THREADS", "1"),
        ("MKL_NUM_THREADS", "1"),
        ("NUMEXPR_NUM_THREADS", "1"),
        ("MNE_DONTWRITE_HOME", "true"),
    ):
        os.environ.setdefault(key, value)
    try:
        status = rerun_path(Path(parquet), unit, dry_run=dry_run)
        return parquet, status, ""
    except Exception as exc:  # noqa: BLE001 — one session must not stop the batch
        return parquet, "FAIL", f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--unit", choices=("uV", "mV", "V"), help="used when info.json has no unit")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    index = json.loads(args.index.read_text(encoding="utf-8"))
    tasks = _tasks(_brainco_paths(index))
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"datasets={len(tasks)} jobs={args.jobs} dry_run={args.dry_run} unit={args.unit}", flush=True)
    if not tasks:
        return 0
    import multiprocessing as mp

    counts: dict[str, int] = {}
    ctx = mp.get_context("spawn")
    work = [(path, args.unit, args.dry_run) for path in tasks]
    with ProcessPoolExecutor(max_workers=max(1, args.jobs), mp_context=ctx) as pool:
        futures = [pool.submit(worker, item) for item in work]
        done = 0
        for fut in as_completed(futures):
            path, status, detail = fut.result()
            done += 1
            counts[status] = counts.get(status, 0) + 1
            if status == "FAIL" or done == len(tasks) or done % 20 == 0:
                print(f"[{done}/{len(tasks)}] {status} {path}", flush=True)
                if detail:
                    print(detail, flush=True)
    print(counts, flush=True)
    return 1 if counts.get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
