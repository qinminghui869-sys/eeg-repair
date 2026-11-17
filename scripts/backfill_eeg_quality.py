#!/usr/bin/env python3
"""Backfill eeg_repair.quality into meta/info.json for already-published sessions.

Recomputes bad_rate with the same filter + impedance + mark_bad_channels path as
repair, without rewriting parquet. Only datasets that already have eeg_repair are
updated. Sessions without a published repair are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from eeg_repair import algorithms as alg  # noqa: E402
from eeg_repair.config import load_config  # noqa: E402
from eeg_repair.dataset import write_json  # noqa: E402
from eeg_repair.parquet import read_recording  # noqa: E402
from eeg_repair.pipeline import impedance_bads, prepare_montage  # noqa: E402


def batch_dirs(root: Path, start: int, end: int) -> list[Path]:
    return [root / f"batch_{i:07d}" for i in range(start, end + 1)]


def sessions_with_repair(root: Path, start: int, end: int) -> list[Path]:
    out = []
    for batch in batch_dirs(root, start, end):
        if not batch.is_dir():
            continue
        for sess in sorted(batch.glob("L3-*")):
            info_path = sess / "meta/info.json"
            if not info_path.is_file():
                continue
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if info.get("eeg_repair"):
                out.append(sess)
    return out


def score_session(sess: Path, config: Path) -> dict:
    eeg = next(sess.rglob("observation.eeg.parquet"))
    cfg = load_config(config, source=eeg)
    record = read_recording(eeg, cfg)
    scalp, names = alg.scalp_names(list(cfg.channels))
    montage = prepare_montage(names, cfg)
    impedance = eeg.with_name("observation.eeg_impedance.parquet")
    prior = impedance_bads(impedance if impedance.exists() else None, names, cfg)
    centered = alg.remove_dc(record.data_uv[scalp])
    clean = alg.bandpass_notch(
        centered,
        names,
        cfg.sfreq,
        l_freq=cfg.highpass_hz,
        h_freq=cfg.lowpass_hz,
        notch_hz=cfg.notch_hz,
    )
    qc = alg.mark_bad_channels(clean, names, cfg.sfreq, montage)
    bads = [n for n in names if n in set(qc["bad_channels"]) | set(prior)]
    rate = len(bads) / len(names)
    quality = (
        alg.quality_from_bad_rate(rate) if rate <= cfg.max_bad_ratio and len(names) - len(bads) >= 4 else None
    )
    return {
        "session": str(sess),
        "bad_rate": float(rate),
        "n_bad": len(bads),
        "n_scalp": len(names),
        "quality": quality,
        "publishable": quality is not None,
    }


def _worker(args: tuple[str, str]) -> dict:
    sess, config = args
    try:
        return {"ok": True, **score_session(Path(sess), Path(config))}
    except Exception as exc:  # noqa: BLE001 — surface per-session failures in the driver
        return {"ok": False, "session": sess, "error": f"{type(exc).__name__}: {exc}"}


def patch_info(sess: Path, scored: dict, *, dry_run: bool) -> None:
    info_path = sess / "meta/info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    er = info["eeg_repair"]
    records = er.get("records") or []
    if not records:
        raise ValueError("eeg_repair.records is empty")
    # L3 sessions are one episode; apply the same score to every record.
    for record in records:
        record["quality"] = scored["quality"]
        record["bad_rate"] = scored["bad_rate"]
        record["n_bad"] = scored["n_bad"]
        record["n_scalp"] = scored["n_scalp"]
    qualities = [r["quality"] for r in records]
    er["quality"] = "good" if "good" in qualities else "excellent"
    er["records"] = records
    info["eeg_repair"] = er
    if dry_run:
        return
    write_json(info_path, info)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=14)
    parser.add_argument("--jobs", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="optional cap for smoke tests")
    args = parser.parse_args(argv)

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    sessions = sessions_with_repair(args.root.resolve(), args.start, args.end)
    if args.limit:
        sessions = sessions[: args.limit]
    print(f"sessions_with_eeg_repair={len(sessions)} dry_run={args.dry_run} jobs={args.jobs}")
    if not sessions:
        return 0

    work = [(str(s), str(args.config.resolve())) for s in sessions]
    excel = good = skip = fail = 0
    failures = []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(_worker, item) for item in work]
        for fut in as_completed(futures):
            result = fut.result()
            if not result["ok"]:
                fail += 1
                failures.append(result)
                print(f"FAIL {result['session']}: {result['error']}")
                continue
            if not result["publishable"]:
                skip += 1
                print(
                    f"SKIP {result['session']}: bad_rate={result['bad_rate']:.4f} "
                    f"({result['n_bad']}/{result['n_scalp']}) exceeds publish gate"
                )
                continue
            patch_info(Path(result["session"]), result, dry_run=args.dry_run)
            if result["quality"] == "excellent":
                excel += 1
            else:
                good += 1
            print(
                f"{'DRY ' if args.dry_run else ''}OK {result['session']}: "
                f"quality={result['quality']} bad_rate={result['bad_rate']:.4f} "
                f"({result['n_bad']}/{result['n_scalp']})"
            )

    summary = {
        "excellent": excel,
        "good": good,
        "skipped_unpublishable": skip,
        "failed": fail,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if failures:
        out = Path(tempfile.gettempdir()) / "eeg_quality_backfill_failures.jsonl"
        out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in failures) + "\n")
        print(f"failures_written={out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
