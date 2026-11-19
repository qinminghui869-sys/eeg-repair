# EEG Repair

CPU 上的 EEG Parquet 修复工具。对头皮导联做预处理后另存结构相同的 Parquet，源文件不覆盖。不下载模型，不改变采样率与通道数。

处理顺序：**阻抗检查 → 滤波 → 坏道检测 → ICA 去伪迹 → 坏道插值 → 平均参考**。阻抗在滤波前读取，判定不使用滤波后的波形。

<table>
  <thead>
    <tr>
      <th>步骤</th>
      <th>参数</th>
      <th>默认值</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="3">阻抗检查</td>
      <td>输入</td>
      <td>同目录 <code>observation.eeg_impedance.parquet</code>；没有该文件则跳过</td>
    </tr>
    <tr>
      <td>坏道阈值</td>
      <td>任一快照 ≥ 150 kΩ</td>
    </tr>
    <tr>
      <td>文件单位</td>
      <td><code>ohm</code>，先换算为 kΩ 再比较</td>
    </tr>
    <tr>
      <td rowspan="5">滤波</td>
      <td>去直流</td>
      <td>每个头皮通道减去自身时间中位数</td>
    </tr>
    <tr>
      <td>带通</td>
      <td>0.5–100 Hz；FIR，零相位，Hamming 窗，firwin</td>
    </tr>
    <tr>
      <td>低通上限</td>
      <td>100 Hz 与 Nyquist−1 Hz 中的较小值</td>
    </tr>
    <tr>
      <td>陷波</td>
      <td>50 Hz；FIR，零相位</td>
    </tr>
    <tr>
      <td>滤波长度、过渡带</td>
      <td>使用 MNE 自动值</td>
    </tr>
    <tr>
      <td rowspan="7">坏道检测</td>
      <td>flat</td>
      <td>标准差 &lt; 0.2 µV，且相邻差分 &lt; 0.02 µV 的占比 &gt; 0.7</td>
    </tr>
    <tr>
      <td>contact</td>
      <td>稳健 z &gt; 3.5，或 z &gt; 3.0 且标准差 &gt; 25 µV；仅 FP1、FP2、FPZ、AF3、AF4 单独命中时豁免</td>
    </tr>
    <tr>
      <td>bridge</td>
      <td>记录短于 10 秒则跳过；2 秒分段，最近 4 个近邻的电学距离过小</td>
    </tr>
    <tr>
      <td>correlation</td>
      <td>1 秒窗口内最大绝对相关 &lt; 0.4 的占比 &gt; 20%；仅此项命中时不记坏道</td>
    </tr>
    <tr>
      <td>hfnoise</td>
      <td>50 Hz 分界的四阶 Butterworth 高低频比，稳健 z &gt; 5；只用于判定，不写入结果</td>
    </tr>
    <tr>
      <td>发布条件</td>
      <td>坏道比例 &gt; 0.60，或好道少于 4 个时，该记录不发布</td>
    </tr>
    <tr>
      <td>质量等级</td>
      <td>已发布记录：≤0.40 为 <code>excellent</code>，0.40–0.60 为 <code>good</code></td>
    </tr>
    <tr>
      <td rowspan="5">ICA 去伪迹</td>
      <td>拟合数据</td>
      <td>另做 1–100 Hz 副本，不串联到输出支路</td>
    </tr>
    <tr>
      <td>算法</td>
      <td>Infomax，最多 32 个成分，随机种子 97，最多 500 次迭代</td>
    </tr>
    <tr>
      <td>最短时长</td>
      <td>短于 5 秒或拟合失败时跳过，继续后续步骤</td>
    </tr>
    <tr>
      <td>肌电</td>
      <td>30–100 Hz 与 1–30 Hz 能量比的稳健 z &gt; 3，剔除数量不超过好道数的 20%</td>
    </tr>
    <tr>
      <td>眼电</td>
      <td>用 FP1、FP2、FPZ 作代理，最多剔除 2 个成分</td>
    </tr>
    <tr>
      <td rowspan="2">坏道插值</td>
      <td>方法</td>
      <td>球面样条，只重建有坐标的坏道</td>
    </tr>
    <tr>
      <td>无坐标通道</td>
      <td>保持滤波和 ICA 后的原值</td>
    </tr>
    <tr>
      <td rowspan="2">平均参考</td>
      <td>范围</td>
      <td>插值后的全部头皮通道</td>
    </tr>
    <tr>
      <td>不参与的通道</td>
      <td>IO、TRIG、TRIGGER、VEOG、HEOG、EKG、EMG，原值保留</td>
    </tr>
  </tbody>
</table>

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

数据集模式把 `observation.eeg_repair.parquet` 写回源数据目录。此时 `--output-dir` 只接收日志。原始 `observation.eeg.parquet` 不覆盖。退出码：`0` 全部成功，`1` 处理或质检失败，`2` 输入或配置错误。预检失败时不继续修复。

`scripts/filter.sh` 只对头皮通道做带通和陷波。它不减去时间中位数，不写质量等级。输入范围与 `scripts/repair.sh` 相同，参数为 `--config`、`--output-dir`、`--pattern`。结果文件名是 `observation.eeg_denoise.parquet`。数据集模式把它写回源目录，并在 `meta/info.json` 增加 `features.observation.eeg_denoise`；此时 `--output-dir` 只接收日志。独立 Parquet 写到 `--output-dir` 下的对应相对目录。源文件不覆盖。

```bash
scripts/filter.sh <input>
scripts/filter.sh <input> --config configs/eeg_repair.yaml --output-dir outputs/filter1
```

| 命令 | 作用 |
| --- | --- |
| `eeg-repair` | 修复并写出结果 |
| `eeg-filter` | 只做带通和陷波并写出结果 |
| `eeg-repair-inspect` | 只读检查输入，并可生成配置模板 |
| `eeg-repair-qc` | 检查空值、缺帧与时间戳，把 JSON 打到标准输出 |

只检查、不修复：

```bash
eeg-repair-inspect <input.parquet> --write-config <config.yaml>
eeg-repair <input> --config <config.yaml> --output-dir <dir> --dry-run
eeg-repair-qc <dataset-dir>
```

`--dry-run` 只校验输入、坐标和输出路径，不写文件。`eeg-repair-qc` 不改输入，目录模式默认匹配 `observation.eeg_repair.parquet`。已发布但还没有质量等级时，用 `scripts/backfill_eeg_quality.py` 按同一套坏道规则回填 `info.json`，不重写 Parquet。

## 输入与输出

每行一个采样点。`columns` 布局每个通道一列，处理范围由 `channels` 指定。`vector` 布局的 `signal_column` 为浮点 list，元素顺序与 `channels` 一致。不支持把整段波形放进一个单元格、ADC 整数，以及自动比例换算。

`timestamp` 为 float64 秒，有限、非负且严格递增，采样率与配置一致。`episode_index` 为文件内恒定的 int64。时间戳沿用输入，不重建、不平移。字段名、类型、嵌套结构、schema 元数据、行数和非脑电字段均保留。辅助通道、发布条件和质量等级见上面的参数表。

数据集模式成功后改这两个文件：

| 文件 | 动作 |
| --- | --- |
| 与源文件同目录的 `observation.eeg_repair.parquet` | 新增修复后的脑电。 |
| 该数据集的 `meta/info.json` | 增加 `features.observation.eeg_repair`，以及 `eeg_repair.quality` 和每条记录的 `quality`、`bad_rate`、`n_bad`、`n_scalp`。路径模板与主时钟字段保持不变。 |

不改 `observation.eeg.parquet`、阻抗文件、`meta/episodes_stats.jsonl` 和其他模态。不另存坏道、ICA 或参数记录。存在旧式 `meta/stats.json` 时拒绝发布。处理中的临时目录在结束时删除。任一 episode 失败时，该数据集本次新增的修复文件会被删除，`meta/info.json` 恢复为运行前内容；同批其他数据集继续。

独立 Parquet 的结果写到 `--output-dir`：成功为 `<stem>_repair.parquet`，失败追加 `quarantine.jsonl`。`scripts/repair.sh` 另在日志目录写入 `logs/preflight.log`、`logs/repair.log`、`logs/output_qc.json` 和 `logs/status.txt`。

发布前校验源文件 SHA-256。结果先写临时文件，回读通过后原子发布且不可覆盖。压缩 codec 与行组大小沿用源文件。结构质检通过或坐标覆盖完整，都不表示帽型定位或生理有效性已验证。ICA 异常可降级继续。

## 配置

单位取 `uV`、`mV` 或 `V`，须来自元数据或配置，程序不根据幅度猜测。配置与元数据冲突时终止。缺少电极坐标时不终止处理，也不补猜位置；这些通道不参与球面插值。实测坐标 CSV 字段为 `name,x_m_mne,y_m_mne,z_m_mne`，单位米，路径相对于配置文件所在目录。

| 文件 | 用途 |
| --- | --- |
| `configs/brainco_31.yaml` | 31 通道顺序与采样率。`unit` 为空时不能直接运行。 |
| `configs/neuroscan_128.yaml` | 128 通道顺序与采样率。`unit` 为空时不能直接运行。标准模板缺少 `F11/F12/FT11/FT12/Cb1/Cb2/Cbz`。 |
| `configs/eeg_8.template.yaml` | 8 通道接入模板。须填写通道、采样率、单位和坐标。 |
| `configs/eeg_repair.yaml` | 250 Hz、µV 的参数示例，不代表特定设备的单位。 |
| `configs/provisional.yaml` | 缺少完整实测坐标时的显式试运行配置。单位仍须按批次确认。 |

`configs/provisional.yaml` 使用仓库内暂定坐标。`F11/F12/FT11/FT12` 采用 [CURRY 公布的 PAN 坐标](https://sccn.ucsd.edu/pipermail/eeglablist/2022/016586.html)，由毫米换算为米；`Cb1/Cb2/Cbz` 对应模板的 `I1/I2/Iz`。这些都不是个体实测。生成方式见 [docs/PROVENANCE.md](docs/PROVENANCE.md)。

## 目录

```text
src/eeg_repair/                  命令、管线、算法、Parquet 与数据集契约
configs/                         设备配置与暂定坐标
scripts/repair.sh                预检、修复、质检
scripts/filter.sh                预检、带通和陷波、质检
scripts/backfill_eeg_quality.py  为已发布数据集回填质量等级
docs/                            算法边界、来源、验证与流程图
tests/                           数值、契约与异常路径
```

`outputs/` 只放本地运行日志和独立 Parquet 结果，已加入 `.gitignore`。提交前检查见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- [算法与实现边界](docs/ALGORITHM.md)
- [来源与迁移](docs/PROVENANCE.md)
- [验证记录](docs/VALIDATION.md)
- [流程图](docs/eeg_repair_flowchart.html)
