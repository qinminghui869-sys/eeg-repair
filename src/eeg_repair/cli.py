"""Single-file and batch CLI with per-file failure isolation."""

import argparse
import json
import logging
import re
import warnings
from pathlib import Path
from datetime import datetime, timezone

from .config import load_config

_REASONS = (
    ("input does not exist", "输入不存在"),
    ("--impedance only", "阻抗文件仅适用于单个文件"),
    ("batch output directory must be outside", "输出目录不能位于输入目录内"),
    ("no matching Parquet", "未找到输入文件"),
    ("repair output already exists", "修复结果已存在"),
    ("denoise output already exists", "滤波结果已存在"),
    ("observation.eeg_denoise already present", "元数据已有滤波记录"),
    ("already present in meta/info.json", "元数据已有修复记录"),
    ("no scalp channels", "没有头皮通道"),
    ("legacy meta/stats.json", "存在旧版 stats.json，已拒绝发布"),
    ("dataset mode requires all EEG", "须处理该数据集的全部脑电文件"),
    ("unique episode_index", "episode 编号重复"),
    ("at least four scalp", "头皮通道少于 4 个"),
    ("montage CSV hash", "坐标文件与配准记录不一致"),
    ("head coordinates", "坐标记录不是米制 head 坐标"),
    ("positions must be distinct", "电极坐标重复"),
    ("empty impedance", "阻抗文件为空"),
    ("impedance file missing", "阻抗文件缺少通道"),
    ("invalid impedance", "阻抗数值无效"),
    ("shorter than min_duration", "记录短于最短时长"),
    ("source changed", "源文件在处理期间发生变化"),
    ("never be overwritten", "禁止覆盖源文件"),
    ("sampling rate", "采样率与配置不一致"),
    ("strictly increasing", "时间戳无效"),
    ("missing recording parameters", "缺少记录参数"),
    ("conflicts with recording", "配置与元数据冲突"),
    ("destination must be new", "输出路径已存在"),
)


def _reason(exc: BaseException) -> str:
    text = str(exc)
    found = re.search(r"insufficient good channels: (\d+)/(\d+) bad; max_bad_ratio=([0-9.]+)", text)
    if found:
        return f"坏道过多：{found.group(1)}/{found.group(2)}，上限 {found.group(3)}"
    for key, label in _REASONS:
        if key in text:
            return label
    return "处理失败"


def _quiet_libraries() -> None:
    warnings.filterwarnings("ignore", module=r"mne")
    warnings.filterwarnings("ignore", message=r"Use rng=")
    import mne

    mne.set_log_level("ERROR")


def _parser(description: str, *, impedance: bool, dry_run_help: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("input", type=Path, help="source Parquet or dataset directory")
    parser.add_argument(
        "--config", type=Path, help="optional algorithm settings / missing metadata parameters"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--pattern", default="observation.eeg.parquet", help="recursive input glob for directory mode"
    )
    if impedance:
        parser.add_argument(
            "--impedance", type=Path, help="single-file mode only; otherwise discover sibling file"
        )
    parser.add_argument("--dry-run", action="store_true", help=dry_run_help)
    return parser


def _resolve(args):
    source = args.input.resolve()
    if not source.exists():
        raise ValueError(f"input does not exist: {source}")
    impedance = getattr(args, "impedance", None)
    if source.is_dir() and impedance:
        raise ValueError("--impedance only supported for a single input file")
    output = args.output_dir.resolve()
    if source.is_dir() and (output == source or source in output.parents):
        raise ValueError("batch output directory must be outside input tree")
    paths = sorted(source.rglob(args.pattern)) if source.is_dir() else [source]
    if not paths:
        raise ValueError("no matching Parquet input files")
    return source, output, paths


def _loose_parent(source: Path, output: Path, path: Path) -> Path:
    relative = path.relative_to(source) if source.is_dir() else Path(path.name)
    return output / relative.parent


def _quarantine(output: Path, path: Path, exc: BaseException) -> None:
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


def _run(args, *, dataset_fn, file_fn, loose_target) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    _quiet_libraries()
    try:
        source, output, paths = _resolve(args)
    except Exception as exc:
        logging.error("失败：%s", _reason(exc))
        return 2
    from .dataset import dataset_root

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
        try:
            dataset_fn(root, members)
            logging.info("成功 %s", root)
        except Exception as exc:
            failed += len(members)
            logging.error("失败 %s：%s", root, _reason(exc))
    for path in loose:
        target = loose_target(source, output, path)
        try:
            cfg = load_config(args.config, source=path)
            file_fn(path, target, cfg)
            logging.info("成功 %s", path)
        except Exception as exc:
            failed += 1
            logging.error("失败 %s：%s", path, _reason(exc))
            if not args.dry_run:
                _quarantine(output, path, exc)
    logging.info("完成：成功 %d，失败 %d", len(paths) - failed, failed)
    return 1 if failed else 0


def main(argv=None) -> int:
    args = _parser(
        "修复 EEG Parquet，不修改源文件",
        impedance=True,
        dry_run_help="validate input, coordinates and output paths only",
    ).parse_args(argv)

    def repair_many(root, members):
        from .dataset import repair_dataset

        return repair_dataset(root, members, args.config, dry_run=args.dry_run, impedance=args.impedance)

    def repair_one(path, target, cfg):
        from .pipeline import repair_file

        return repair_file(path, target, cfg, impedance=args.impedance, dry_run=args.dry_run)

    def loose_target(source, output, path):
        return _loose_parent(source, output, path) / (path.stem + "_repair.parquet")

    return _run(args, dataset_fn=repair_many, file_fn=repair_one, loose_target=loose_target)


def filter_main(argv=None) -> int:
    args = _parser(
        "对 EEG Parquet 做带通和陷波，不修改源文件",
        impedance=False,
        dry_run_help="validate input and output paths only",
    ).parse_args(argv)

    def filter_many(root, members):
        from .dataset import filter_dataset

        return filter_dataset(root, members, args.config, dry_run=args.dry_run)

    def filter_one(path, target, cfg):
        from .pipeline import filter_file

        return filter_file(path, target, cfg, dry_run=args.dry_run)

    def loose_target(source, output, path):
        return _loose_parent(source, output, path) / "observation.eeg_denoise.parquet"

    return _run(args, dataset_fn=filter_many, file_fn=filter_one, loose_target=loose_target)
