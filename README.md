# EEG Repair

CPU 上的 EEG Parquet 修复工具。对头皮导联做预处理后另存结构相同的 Parquet，源文件不覆盖。不下载模型，不改变采样率与通道数。

处理顺序：**滤波 → 阻抗检查 → 坏道检测 → ICA 去伪迹 → 坏道插值 → 平均参考**。

## 环境

Python 3.11 或 3.12。

```bash
uv sync --locked --extra dev --python 3.11
source .venv/bin/activate
```

未使用 uv 时执行 `python -m pip install -e '.[dev]'`。该方式不读取锁文件，依赖版本可能与 CI 不一致。

## 运行

`scripts/repair.sh` 依次做预检、修复和结构质检，日志写入运行目录。

```bash
scripts/repair.sh <input>
scripts/repair.sh <input> --config configs/provisional.yaml
scripts/repair.sh <file.parquet> --config configs/eeg_repair.yaml --output-dir outputs/run1
```

`<input>` 可以是单个 Parquet、含 `meta/info.json` 的数据集目录，或一批数据集的上级目录。

| 参数 | 作用 |
| --- | --- |
| `--config FILE` | 补充缺失元数据，或指定滤波参数与电极坐标。数据集模式可省略。 |
| `--output-dir DIR` | 日志目录；独立 Parquet 的结果也写在这里。默认 `<repo>/outputs/run_XXXXXX`。 |
| `--pattern GLOB` | 目录模式下的输入文件名，默认 `observation.eeg.parquet`。 |
| `--impedance FILE` | 仅单文件模式。目录模式自动读取同目录的 `observation.eeg_impedance.parquet`。 |

数据集模式把 `observation.eeg_repair.parquet` 写回源数据目录。此时 `--output-dir` 只接收日志。原始 `observation.eeg.parquet` 不覆盖。

退出码：`0` 全部成功，`1` 处理或质检失败，`2` 输入或配置错误。预检失败时不继续修复。

## 命令

| 命令 | 作用 |
| --- | --- |
| `eeg-repair` | 修复并写出结果与审计 |
| `eeg-repair-inspect` | 只读检查输入，并可生成配置模板 |
| `eeg-repair-qc` | 检查空值、缺帧与时间戳约定 |

只检查、不修复：

```bash
eeg-repair-inspect <input.parquet> --write-config <config.yaml>
eeg-repair <input> --config <config.yaml> --output-dir <dir> --dry-run
eeg-repair-qc <dataset-dir>
```

`--dry-run` 只校验输入、坐标和输出路径，不写文件。`eeg-repair-qc` 把 JSON 打到标准输出，不改输入。目录模式默认匹配 `observation.eeg_repair.parquet`。

## 输入与输出

每行一个采样点。

| 布局 | 要求 |
| --- | --- |
| `columns` | 每个通道一列，处理范围由 `channels` 指定。 |
| `vector` | `signal_column` 为浮点 list，元素顺序与 `channels` 一致。 |

不支持把整段波形放进一个单元格、ADC 整数，以及自动比例换算。

`timestamp` 为 float64 秒，有限、非负且严格递增，采样率与配置一致。`episode_index` 为文件内恒定的 int64。时间戳沿用输入，不重建、不平移。字段名、类型、嵌套结构、schema 元数据、行数和非脑电字段均保留。

`IO`、`TRIG`、`VEOG`、`HEOG`、`EKG`、`EMG` 不参与滤波、ICA、插值和平均参考，原值保留。可用头皮通道不得少于 4 个。坏道比例大于 40% 时该记录不发布。阻抗快照中任一值不低于 150 kΩ 的通道记为坏道。

| 输入 | EEG 结果 | 审计 |
| --- | --- | --- |
| 含 `meta/info.json` 的数据集 | 同目录新增 `observation.eeg_repair.parquet` | `meta/observation.eeg.audit.json`；多 episode 时为 `meta/episode_XXXXXX/` |
| 独立 Parquet | `<output-dir>/.../<stem>_repair.parquet` | 同目录 `<stem>_repair.audit.json` |

数据集模式同时更新该数据集的 `meta/info.json`（新增 `features.observation.eeg_repair`）和 `meta/episodes_stats.jsonl`。原 `observation.eeg` 及其他模态统计不变。存在旧式 `meta/stats.json` 时拒绝发布。

任一 episode 失败时，该数据集已写入的修复文件与元数据会回滚，同批其他数据集继续。独立 Parquet 失败写入 `<output-dir>/quarantine.jsonl`。

发布前校验源文件 SHA-256。结果先写临时文件，回读通过后原子发布且不可覆盖。压缩 codec 与行组大小沿用源文件。

审计记录参数、坏道与 ICA 决策、规则命中及输入输出哈希，须与结果一并保留。结构质检通过、坐标覆盖完整或审计标记 `SUCCESS`，都不表示帽型定位或生理有效性已验证。ICA 异常可降级继续，须查看审计中的 `ica.skipped`。

## 配置

单位取 `uV`、`mV` 或 `V`，须来自元数据或配置，程序不根据幅度猜测。配置与元数据冲突时终止。头皮通道均须有电极坐标。实测坐标 CSV 字段为 `name,x_m_mne,y_m_mne,z_m_mne`，单位米，坐标系为 MNE head，路径相对于配置文件所在目录。

| 文件 | 用途 |
| --- | --- |
| `configs/brainco_31.yaml` | 31 通道顺序与采样率。`unit` 为空时不能直接运行。 |
| `configs/neuroscan_128.yaml` | 128 通道顺序与采样率。`unit` 为空时不能直接运行；标准 montage 未覆盖的电极须提供完整坐标 CSV。 |
| `configs/eeg_8.template.yaml` | 8 通道接入模板。须填写通道、采样率、单位和坐标。 |
| `configs/eeg_repair.yaml` | 250 Hz、µV 的参数示例，不代表特定设备的单位。 |
| `configs/provisional.yaml` | 缺少完整实测坐标时的显式试运行配置。单位仍须按批次确认。 |

`configs/provisional.yaml` 使用仓库内暂定坐标。它不是个体实测，也不等同于厂家完整 128 导帽验证。生成方式与来源见 [docs/PROVENANCE.md](docs/PROVENANCE.md)。正式配置不会为缺失电极补位。

## 目录

```text
src/eeg_repair/    命令、管线、算法、Parquet 与数据集契约
configs/           设备配置与暂定坐标
scripts/repair.sh  预检、修复、质检
docs/              算法边界、来源、验证与流程图
tests/             数值、契约与异常路径
```

`outputs/` 只放本地运行日志和独立 Parquet 结果，已加入 `.gitignore`。

## 开发

```bash
ruff check src tests
ruff format --check src tests
pytest -q
```

设置 `EEG_TEST_DATA` 指向本地数据集可启用真实 Parquet 契约测试；未设置时相关用例跳过。测试通过表示实现符合上述约定，不代表设备数据的生理有效性已验收。提交前检查与代码边界见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- [算法与实现边界](docs/ALGORITHM.md)
- [来源与迁移](docs/PROVENANCE.md)
- [验证记录](docs/VALIDATION.md)
- [流程图](docs/eeg_repair_flowchart.html)
