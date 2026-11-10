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
    ("already present in meta/info.json", "元数据已有修复记录"),
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="修复 EEG Parquet，不修改源文件")
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
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    _quiet_libraries()
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
        logging.error("失败：%s", _reason(exc))
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
        try:
            repair_dataset(root, members, args.config, dry_run=args.dry_run, impedance=args.impedance)
            logging.info("成功 %s", root)
        except Exception as exc:
            failed += len(members)
            logging.error("失败 %s：%s", root, _reason(exc))
    for path in loose:
        relative = path.relative_to(source) if source.is_dir() else Path(path.name)
        target = output / relative.parent / (relative.stem + "_repair.parquet")
        try:
            cfg = load_config(args.config, source=path)
            repair_file(path, target, cfg, impedance=args.impedance, dry_run=args.dry_run)
            logging.info("成功 %s", path)
        except Exception as exc:
            failed += 1
            logging.error("失败 %s：%s", path, _reason(exc))
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
    logging.info("完成：成功 %d，失败 %d", len(paths) - failed, failed)
    return 1 if failed else 0
