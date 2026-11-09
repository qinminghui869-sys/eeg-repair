# EEG Repair

面向 EEG Parquet 的 CPU 修复工具。对原始头皮导联完成预处理后，另存结构相同的 Parquet。源文件保持不变。

处理在 CPU 上完成，不下载模型，不改变采样率与通道数。

处理顺序：**滤波 → 阻抗检查 → 坏道检测 → ICA 去伪迹 → 坏道插值 → 平均参考**。

命令：

| 命令 | 作用 |
| --- | --- |
| `eeg-repair` | 修复 EEG，并写出结果与审计文件 |
| `eeg-repair-inspect` | 只读检查输入，并可生成配置模板 |
| `eeg-repair-qc` | 检查修复结果的空值、缺帧与时间戳约定 |

## 安装

要求 Python 3.11 或 3.12。建议使用仓库中的 `uv.lock` 安装锁定版本：

```bash
uv sync --locked --extra dev --python 3.11
source .venv/bin/activate
```

未使用 uv 时，可执行 `python -m pip install -e '.[dev]'`。该方式不读取锁文件，依赖版本可能与 CI 不一致。

## 使用

### 检查输入

```bash
eeg-repair-inspect <input.parquet> --write-config <config.yaml>
```

检查结果包含通道名称及顺序、采样率、单位和电极位置。单位取值为 `uV`、`mV` 或 `V`，须来自元数据或配置文件。

头皮通道均须具备电极坐标。实测坐标使用 CSV，字段如下，单位为米，坐标系为 MNE head：

```text
name,x_m_mne,y_m_mne,z_m_mne
```

配置文件中的 CSV 路径相对于该配置文件所在目录。

### 校验

```bash
eeg-repair <input> --config <config.yaml> --output-dir <output-dir> --dry-run
```

`--dry-run` 仅校验输入、坐标和输出路径，不执行修复，不写入文件。

### 执行

```bash
eeg-repair <input.parquet> --config <config.yaml> --output-dir <output-dir>

eeg-repair <input-dir> --config <config.yaml> --output-dir <output-dir>
```

批处理时，输出目录须位于输入目录之外，且必须为新建目录。

### 质检

```bash
eeg-repair-qc <observation.eeg_repair.parquet>
eeg-repair-qc <dataset-dir>
eeg-repair-qc <dataset-dir> --pattern observation.eeg.parquet
```

`eeg-repair-qc` 检查结果是否符合结构约定：空文件、通道缺失、空值、非有限值、`timestamp` / `episode_index` 类型与取值、重复或回退时间戳，以及超过 1.5 个采样间隔的缺帧。结果以 JSON 打印到标准输出，不写文件，不修改输入。目录模式默认匹配 `observation.eeg_repair.parquet`。

## 数据集模式

输入可追溯到 `meta/info.json` 时，`--config` 可以省略。程序读取 `features.observation.eeg` 的通道、采样率和单位，并按 Parquet 的 `episode_index` 在 `meta/collect_info.jsonl` 中匹配 `hardware.eeg`。同一批次可包含多台设备。

`--config` 用于补充缺失参数，或指定滤波参数与电极坐标。配置与元数据冲突时终止。单位缺失时，须在配置中显式填写。`eeg.npz` 不属于当前输入接口。

数据集模式须从数据集根目录启动，并包含该数据集的全部 EEG episode。输出为完整副本，包含视频等大文件，须预留相应磁盘空间。

## 输出

| 输入 | EEG 结果 | 审计文件 |
| --- | --- | --- |
| 含 `meta/info.json` 的数据集 | 保持原相对路径的 `observation.eeg.parquet` | `meta/observation.eeg.audit.json`；多 episode 时为 `meta/episode_XXXXXX/observation.eeg.audit.json` |
| 独立 Parquet | `observation.eeg_repair.parquet` | 同目录 `observation.eeg_repair.audit.json` |

数据集副本同时完成以下更新：

- 复制其余模态、索引和元数据。
- 在输出 `meta/info.json` 中写入已确认的 EEG 单位、修复记录及审计文件相对路径。
- 重算 `meta/episodes_stats.jsonl` 中 EEG 的 min、max、mean、std、count；其余特征统计保持不变。
- 保留通道、shape、dtype、采样率、对齐窗口、路径模板，以及 episode 与 frame 计数。时间戳保持原值。

副本中的原 QC 文件保持采集时的内容。修复质量以新生成的审计文件为准。审计文件记录修复参数、坏道与 ICA 决策、规则命中及输入输出哈希，不能由 Parquet 重建，须与结果一并保留。

## 数据要求

每行对应一个采样点。输出须满足 mf-LeRobot / Embodied-Brain-Collect 打包后的 EEG 时间戳约定。

| 布局 | 要求 |
| --- | --- |
| `columns` | 每个通道一列，处理范围由 `channels` 指定。 |
| `vector` | `signal_column` 为浮点 list 或 fixed-size list，元素顺序与 `channels` 一致。 |

不支持单单元格存放整段波形、ADC 整数，以及自动比例换算。

字段名称、顺序、类型、嵌套结构与 schema 元数据均予保留，同时保留时间戳、`episode_index`、行数、通道顺序和非脑电字段。

`IO`、`TRIG`、`VEOG`、`HEOG`、`EKG`、`EMG` 等辅助导联不参与滤波、ICA、插值和平均参考，原值保留。

`timestamp` 须为 float64 秒，与每一行采样一一对应，有限、非负且严格递增，采样率须与配置一致。`episode_index` 须为 int64，且在单个文件内保持不变。时间戳沿用输入值，不重建、不平移。

同目录存在 `observation.eeg_impedance.parquet` 时自动读取；单文件模式可通过 `--impedance` 指定。阻抗文件为各通道一列的数值快照，单位在配置中声明。任一快照不低于 150 kΩ 时，该通道判为坏道。

可用头皮通道不得少于 4 个。坏道比例超过上限时，该记录不予发布。

## 退出码与发布

| 退出码 | 含义 |
| --- | --- |
| 0 | 全部成功 |
| 1 | 存在处理失败 |
| 2 | 输入或配置错误 |

数据集中任一 episode 失败时，该数据集整体不予发布；同一批次中的其他数据集继续处理。错误写入日志。独立 Parquet 失败时，记录追加至输出目录的 `quarantine.jsonl`。

发布前校验源文件 SHA-256。结果先写入临时文件，回读校验通过后，以原子且不可覆盖的方式发布。

输出保留源文件的压缩 codec 与行组大小。字典编码、页布局、压缩级别和 writer 标识不保证逐字节一致。修复后的信号按原浮点类型存储。

存在旧式 `meta/stats.json` 时拒绝发布，以避免保留过期统计。

## 配置文件

| 文件 | 说明 |
| --- | --- |
| `configs/brainco_31.yaml` | 31 通道设备的通道顺序与采样率。`unit` 为空时不可直接运行。 |
| `configs/neuroscan_128.yaml` | 128 通道设备的通道顺序与采样率。`unit` 为空时不可直接运行；标准 montage 未覆盖的电极须提供完整坐标 CSV。 |
| `configs/eeg_8.template.yaml` | 8 通道接入模板。须填入通道、采样率、单位和坐标。 |
| `configs/eeg_repair.yaml` | 250 Hz、µV 的通用参数示例，不代表特定设备的单位设定。 |

坐标未覆盖的电极不会映射到其他位置。通道数较少时，插值结果须结合采集数据评估。

## 目录结构

```text
src/eeg_repair/
  cli.py          修复命令入口
  inspection.py   只读检查与配置模板生成
  quality.py      空值、缺帧与时间戳质检
  config.py       配置校验
  metadata.py     通道、采样率、单位和设备名读取
  parquet.py      读取、校验与原子写出
  pipeline.py     处理阶段编排
  algorithms.py   坏道检测、ICA、插值与平均参考
  dataset.py      数据集副本、meta 审计路径与统计更新
configs/          设备配置与示例
docs/             算法说明、来源与流程图
tests/            数值、Parquet 保真、质检及异常路径测试
```

## 开发检查

```bash
ruff check src tests
ruff format --check src tests
pytest -q
```

测试覆盖 8 通道算法路径、ICA 执行与失败降级、球面插值、辅助通道保留、三种单位、两种布局、时间戳校验、质检规则、源文件防覆盖及变更检测。测试通过表示实现符合上述约定；设备数据的生理有效性须另行评估。

## 相关文档

- 算法与实现边界：[docs/ALGORITHM.md](docs/ALGORITHM.md)
- 来源说明：[docs/PROVENANCE.md](docs/PROVENANCE.md)
- 流程图：[docs/eeg_repair_flowchart.html](docs/eeg_repair_flowchart.html)
