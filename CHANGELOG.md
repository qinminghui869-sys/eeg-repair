# Changelog

## 0.1.0 — 2026-11-06

- 从原项目提取独立 EEG Parquet 修复包，移除模型重建依赖。
- 复用上传的五指标、Infomax ICA、球面样条插值及平均参考算法。
- 增加显式单位和 montage 校验、可选阻抗检测、批处理失败隔离。
- 保留源 schema、非脑电字段、压缩 codec 与行组，增加防覆盖输出和 SHA-256 审计。
- 提供 31/128 通道样本配置、8 通道接入模板、文档及测试/CI。

## Unreleased

- 删除 `configs/brainco_31.yaml`、`configs/eeg_8.template.yaml` 和 `configs/eeg_repair.yaml`。带元数据的记录从 `info.json` 读取通道名、采样率和单位。暂定坐标只覆盖 `configs/neuroscan_128.yaml` 的 128 个头皮通道。
- 增加 BrainCo 列名订正和修复重跑脚本。订正只改列名、不重排数据，并覆盖原 Parquet。
- 说明收成操作说明、实现边界、坐标来源和验证范围，同一事实不再多处重复。
- 数据集模式把 `observation.eeg_repair.parquet` 写回源目录，并在 `meta/info.json` 登记特征与单位。源 Parquet 不覆盖。失败时回滚该数据集本次新增文件。
- 已发布记录写入 `eeg_repair.quality`，以及每条记录的 `quality`、`bad_rate`、`n_bad`、`n_scalp`。坏道比例大于 0.60 或好道少于 4 个则不发布；不超过 0.40 为 `excellent`，其余已发布记录为 `good`。
- 增加 `scripts/repair.sh` 与 `scripts/backfill_eeg_quality.py`。回填只改 `info.json`，不重写 Parquet。
- 运行时不写审计文件，也不改 `meta/episodes_stats.jsonl`。
- 增加 `eeg-repair-qc`。`timestamp` 为 float64 秒且严格递增，`episode_index` 为文件内恒定 int64。
- 默认陷波只做 50 Hz。缺少坐标时不终止、不猜位置，这些通道不参与球面插值。
- `F11/F12/FT11/FT12` 使用公布的 PAN 坐标（毫米换算为米）。试运行配置为 `configs/provisional.yaml`。
