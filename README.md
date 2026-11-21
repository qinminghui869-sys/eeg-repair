# EEG Repair

在 CPU 上预处理 EEG Parquet，另存结构相同的结果。不覆盖源文件，不改变采样率、通道数和采样时刻，不下载模型。

| 入口 | 处理 | 结果 |
| --- | --- | --- |
| `scripts/repair.sh` | 阻抗、去直流、带通、陷波、坏道、ICA、球面插值、平均参考 | `observation.eeg_repair.parquet`，并写质量等级 |
| `scripts/filter.sh` | 只对头皮通道做带通和陷波 | `observation.eeg_denoise.parquet`，不写质量等级 |

`filter.sh` 不减时间中位数，也不做坏道、ICA、插值和重参考。参数见下方修复表中的带通与陷波两行。

## 安装

Python 3.11 或 3.12。

```bash
uv sync --locked --extra dev --python 3.11
source .venv/bin/activate
```

不用 uv 时执行 `python -m pip install -e '.[dev]'`。该方式不读锁文件，版本可能与 CI 不一致。

## 运行

`<input>` 可以是单个 Parquet、含 `meta/info.json` 的数据集目录，或一批数据集的上级目录。

```bash
scripts/repair.sh <input>
scripts/repair.sh <input> --config configs/provisional.yaml
scripts/filter.sh <input>
```

| 参数 | 作用 |
| --- | --- |
| `--config FILE` | 补缺失元数据，或指定滤波参数与电极坐标。有 `info.json` 时可省略。 |
| `--output-dir DIR` | 日志目录。独立 Parquet 的结果也写在这里。默认 `<repo>/outputs/run_XXXXXX`。 |
| `--pattern GLOB` | 目录模式的输入文件名，默认 `observation.eeg.parquet`。 |
| `--impedance FILE` | 仅单文件。目录模式自动读同目录的 `observation.eeg_impedance.parquet`。 |

数据集模式把结果写回源目录，`--output-dir` 只收日志。预检失败不继续处理。退出码：`0` 全部成功，`1` 处理或质检失败，`2` 输入或配置错误。

对应命令：`eeg-repair`、`eeg-filter`、`eeg-repair-inspect`（只读检查，可写配置模板）、`eeg-repair-qc`（检查空值、缺帧和时间戳）。`--dry-run` 只校验输入、坐标和输出路径。`eeg-repair-qc` 默认匹配 `observation.eeg_repair.parquet`。已发布但没有质量等级时，用 `scripts/backfill_eeg_quality.py` 回填 `info.json`，不重写 Parquet。

修复参数：

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

## 输入与输出

每行一个采样点。`columns` 布局每个通道一列；`vector` 布局的 `signal_column` 是浮点 list，顺序与 `channels` 一致。不支持单格整段波形、ADC 整数，以及按幅度自动换算单位。

`timestamp` 为 float64 秒，有限、非负、严格递增，采样率与配置一致。`episode_index` 为文件内恒定的 int64。时间戳沿用输入。字段名、类型、嵌套、schema 元数据、行数和非脑电字段保留。辅助通道原样保留，名单见参数表。

数据集模式只改两处：

| 文件 | 动作 |
| --- | --- |
| 源目录中的 `observation.eeg_repair.parquet` 或 `observation.eeg_denoise.parquet` | 新增结果 |
| `meta/info.json` | 修复增加 `features.observation.eeg_repair` 和质量字段；滤波增加 `features.observation.eeg_denoise`。路径模板和主时钟沿用源特征，单位写成实际使用值 |

质量字段为 `eeg_repair.quality`，以及每条记录的 `quality`、`bad_rate`、`n_bad`、`n_scalp`。不改源 EEG、阻抗、`meta/episodes_stats.jsonl` 和其他模态。不另存坏道、ICA 或参数记录。存在旧式 `meta/stats.json` 时拒绝发布。任一 episode 失败时，删除该数据集本次新增文件并恢复 `info.json`；同批其他数据集继续。

独立 Parquet 写到 `--output-dir` 下的对应相对目录：修复成功为 `<stem>_repair.parquet`，滤波成功为 `observation.eeg_denoise.parquet`，失败追加 `quarantine.jsonl`。`scripts/repair.sh` 另写 `logs/preflight.log`、`logs/repair.log`、`logs/output_qc.json`、`logs/status.txt`。

发布前校验源文件 SHA-256。结果先写临时文件，回读通过后原子发布，且不覆盖已有结果。压缩 codec 与行组大小沿用源文件。

## 配置

单位取 `uV`、`mV` 或 `V`，必须来自元数据或配置，程序不根据幅度猜测。配置与元数据冲突时终止。缺电极坐标时不终止、不猜测位置，这些通道不参与球面插值。实测坐标 CSV 列为 `name,x_m_mne,y_m_mne,z_m_mne`，单位米，路径相对于配置文件。

带 `meta/info.json` 的数据集从 `features.observation.eeg` 读取通道名、采样率和单位。

| 文件 | 用途 |
| --- | --- |
| `configs/neuroscan_128.yaml` | Neuroscan 128 个头皮通道加 `VEOG`、`HEOG`、`EKG`、`EMG`。`unit` 为空时不能直接运行。标准模板缺少 `F11/F12/FT11/FT12/Cb1/Cb2/Cbz`。 |
| `configs/provisional.yaml` | 显式试运行。通道名和采样率仍取元数据；单位须按批次确认。坐标不是个体实测，见 [来源](docs/PROVENANCE.md)。 |

## 数据订正

下面两支脚本会覆盖源文件，只用于 BrainCo 存量数据：列顺序已是硬件顺序，列名仍是旧名单。日常预处理用上面的两条入口。

```bash
python scripts/fix_brainco_parquet_channel_names.py <file.parquet>
python scripts/rerun_brainco_repair.py --index <index.json> --unit uV --jobs 8
```

第一支只改列名，不重排数据。第二支把 `info.json` 中的通道名、形状和空单位改成与 Parquet 一致，再覆盖已有的 `observation.eeg_repair.parquet`。

## 文档

- [实现边界](docs/ALGORITHM.md)：和参考流程的差别，以及不在参数表里的判定
- [坐标来源](docs/PROVENANCE.md)：暂定坐标怎么来、能证明什么
- [验证范围](docs/VALIDATION.md)：测试覆盖了什么、没有覆盖什么
- [流程图](docs/eeg_repair_flowchart.html)
- [开发约定](CONTRIBUTING.md)

`docs/reference/` 是迁入前的流程存档，不是执行入口。`outputs/` 已忽略。
