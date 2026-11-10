# Changelog

## 0.1.0 — 2026-11-06

- 从原项目提取独立 EEG Parquet 修复包，移除模型重建依赖。
- 复用上传的五指标、Infomax ICA、球面样条插值及平均参考算法。
- 增加显式单位和 montage 校验、可选阻抗检测、批处理失败隔离。
- 保留源 schema、非脑电字段、压缩 codec 与行组，增加防覆盖输出和 SHA-256 审计。
- 提供 31/128 通道样本配置、8 通道接入模板、文档及测试/CI。

## Unreleased

- 保留修复审计文件：其中的参数、坏道、ICA 与哈希不能由 Parquet 重建。
- 数据集模式将 `observation.eeg.audit.json` 写入 `meta/`。
- 增加 `eeg-repair-qc`，检查修复结果的空值、非有限值、时间戳与缺帧。
- 修复结果的 `timestamp` 限定为 float64 秒且严格递增，`episode_index` 限定为文件内恒定的 int64。
- 按文件读取 mf-LeRobot 通道、采样率和设备名称，校验配置冲突。
- 数据集模式输出完整独立副本，更新 meta/info.json 处理记录和 EEG 单位。
- 重算逐 episode 的 EEG 统计，保留其他模态、时间轴、索引和路径模板。
- 数据集级失败回滚，全部 episode 成功后发布；独立 Parquet 模式保持兼容。
- 增加可选真实 EEG 数据契约测试（`EEG_TEST_DATA`）和数据集输出回归测试。
- 暂定坐标资产与试运行配置改为通用命名（`configs/provisional.yaml`）。
- 数据集模式把 `observation.eeg_repair.parquet` 写回原数据目录，并在该数据集的 `meta/info.json` 登记对应特征。原始 `observation.eeg.parquet` 不覆盖。
- 增加通用脚本 `scripts/repair.sh`，依次执行预检、修复和结构质检。
- 默认陷波改为只做 50 Hz，与参考预处理一致。
- 缺少电极坐标时不再终止处理：不猜测位置，这些通道不参与球面插值。
- `F11/F12/FT11/FT12` 改为使用 CURRY 公布的 PAN 坐标（毫米换算为米），不再使用相似变换结果。
