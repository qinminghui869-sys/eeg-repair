"""Single-file and batch CLI with per-file failure isolation."""

import argparse
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from .config import load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EEG Parquet repair; never modifies source files")
    parser.add_argument("input", type=Path, help="source Parquet or dataset directory")
    parser.add_argument(
        "--config", type=Path, help="optional algorithm settings / missing metadata parameters"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--pattern", default="observation.eeg.parquet", help="recursive input glob for directory mode"
    )
    parser.add_argument(
        "--impedance", type=Path, help="single-file mode only; otherwise discover sibling file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate input, coordinates and output paths only"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        source = args.input.resolve()
        if not source.exists():
            raise ValueError(f"input does not exist: {source}")
        if source.is_dir() and args.impedance:
            raise ValueError("--impedance only supported for a single input file")
        output = args.output_dir.resolve()
        if source.is_dir() and (output == source or source in output.parents):
            raise ValueError("batch output directory must be outside input tree")
        paths = sorted(source.rglob(args.pattern)) if source.is_dir() else [source]
        if not paths:
            raise ValueError("no matching Parquet input files")
        from .pipeline import repair_file
    except Exception as exc:
        logging.error("configuration/input error: %s", exc)
        return 2
    from .dataset import dataset_root, repair_dataset

    groups = {}
    loose = []
    for path in paths:
        root = dataset_root(path)
        if root is None:
            loose.append(path)
        else:
            groups.setdefault(root, []).append(path)
    failed = 0
    for root, members in groups.items():
        relative = root.relative_to(source) if source.is_dir() and source in root.parents else Path()
        try:
            result = repair_dataset(
                root, output / relative, members, args.config, dry_run=args.dry_run, impedance=args.impedance
            )
            logging.info("%s dataset %s", result["status"], root)
        except Exception as exc:
            failed += len(members)
            logging.error("DATASET FAILED %s: %s", root, exc)
    for path in loose:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        target = output / relative.parent / (relative.stem + "_repair.parquet")
        try:
            cfg = load_config(args.config, source=path)
            result = repair_file(path, target, cfg, impedance=args.impedance, dry_run=args.dry_run)
            logging.info("%s %s", result["status"], path)
        except Exception as exc:
            failed += 1
            logging.error("QUARANTINED %s: %s", path, exc)
            if not args.dry_run:
                output.mkdir(parents=True, exist_ok=True)
                with (output / "quarantine.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "source": str(path),
                                "error": f"{type(exc).__name__}: {exc}",
                                "time": datetime.now(timezone.utc).isoformat(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    logging.info("finished: %d succeeded; %d failed", len(paths) - failed, failed)
    return 1 if failed else 0
