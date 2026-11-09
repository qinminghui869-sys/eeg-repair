# BrainCo EEG 预处理步骤与算法

设备设定：250 Hz，约 31 头皮导联（排除 IO / TRIG），montage 为 standard 10–20。

整体顺序：

```
trial 切分 → video 段通道质检
    → 两路滤波（最终 0.5–100 Hz；ICA 拟合 1–100 Hz）
    → session 内干净 video 拼接，拟合一次 ICA
    → 权重套回每个 trial 的 0.5–100 Hz 整段
    → 按该 trial 的坏道插值 → 平均重参考
```

---

## 1. Trial 切分与阶段划分

- 将 marker 对齐到 EEG 采样点。
- 每个 trial 从本 trial 的 `FIX_ON` 切到下一 trial 的 `FIX_ON`（末段到 `RUN_END` 或文件末尾）。
- 阶段（phase）由 marker 起止定义：

| 阶段 | 起止 | 用途 |
|------|------|------|
| image | IMG_START → IMG_END | 看第一帧想象 |
| video | VIDEO_START → VIDEO_END | 质检，以及 ICA 拟合用的片段 |
| rate | RATE_ON → RATE_OFF | 评分 |
| replay | REPLAY_START → REPLAY_END | 再看视频打标 |
| play_qc | PLAY_QC_ON → PLAY_QC_OFF | 视频本身是否有问题 |

质检和 ICA 拟合都只用 **video** 段。滤波、ICA 写回、插值和重参考作用于整段 trial。

---

## 2. 通道质检（仅 video 段）

对每个 trial 的 video 段，排除辅助通道后，按五条规则判坏道。通过标准：**坏道率 < 0.30**。

### 2.1 Flat（死道 / 平坦）

同时满足：

- 通道 `std < 0.2 µV`
- 相邻采样差分绝对值 `< 0.02 µV` 的比例 `> 0.7`

→ 判为 flat。

### 2.2 Contact（接触差 / 相对高振幅）

对通道 std 做稳健 z 分数（中位数 + MAD，scale≈1.4826）：

- **hard**：`robust_std_z > 3.5`
- **soft**：`robust_std_z > 3.0` 且 `std_uv > 25`

→ 判为 contact。

**豁免**：仅 `Fp1/Fp2/Fpz/AF3/AF4`，且只有 contact、无其它规则命中时，**不计入坏道**。`F7/F8` 不豁免。

### 2.3 Bridge（电极桥接）

信号短于 10 s 则跳过。否则：

1. 按约 2 s 切 epoch，对每对通道算 \(ED = \mathrm{var}_i + \mathrm{var}_j - 2\,\mathrm{cov}_{ij}\)
2. 取各 epoch 的 median ED
3. 用 montage 上 4 个空间近邻（无坐标时用 ED 近邻）限定候选对
4. 多数 epoch 的 ED `< 16 µV²`，且这对的 median ED 不超过它与其它近邻距离的一半 → 判为桥接

两端通道均计入坏道。

### 2.4 Correlation（相关过低）

1. 按约 1 s 切窗，计算通道间相关
2. 每窗取该通道与其它通道的最大绝对相关
3. 最大相关 `< 0.4` 的窗口比例 `> 20%` → 命中 correlation

判坏规则：

- **仅** correlation 命中 → 只记预警，**不计入坏道**
- 与 flat / contact / bridge / hfnoise 任一同时命中 → **计入坏道**

### 2.5 HF noise（高频噪声）

1. 以 50 Hz 为界，计算高频相对低频的噪声指数
2. 对通道做中位数 + MAD 的 z 分数
3. `z > 5` → 判为 hfnoise，计入坏道

### 2.6 坏道汇总

```
is_bad = flat ∨ bridge ∨ hfnoise ∨ (contact ∧ ¬豁免) ∨ (correlation ∧ 其它规则)
```

坏道率 = 坏道数 / 头皮导联数；`< 0.30` 为 PASS，否则 FAIL。

---

## 3. 信号预处理（整段 trial）

坏道来自上一节该 trial 的 QC 结果。ICA 在整个 session 上拟合一次，再写回每个 trial。

### 3.1 Median center

各通道减去自身时间中位数，去掉直流。最终数据和 ICA 拟合拷贝都从这份去直流后的数据分别滤波，不串联两级高通。

### 3.2 带通 + 陷波

两路使用相同的陷波（**50 Hz**）和低通（**100 Hz**），高通不同：

| 用途 | 带通 |
|------|------|
| 最终数据（插值、重参考、保存） | **0.5–100 Hz** |
| ICA 拟合 | **1–100 Hz** |

拟合得到的空间权重套回 0.5–100 Hz 的数据。两路通道、坏道和参考一致。

### 3.3 ICA：去肌电 + 眼电（同一 session 拟合一次）

**拟合数据**

- 同一被试、同一 session 内，取质检通过（坏道率 `< 0.30`）的 video 段，使用 **1–100 Hz** 拷贝，沿时间拼在一起，只拟合一次 Infomax。
- 这些片段里出现过的坏道取**并集**，整段拟合都不用它们。并集只决定谁不进入 ICA；每个 trial 后来仍按自己的坏道插值。
- 没有通过段，或拼接后不足 **5 s** 时，改用该 session 的全部 video 段。仍不足 5 s 则本 session 改为按 trial 单独拟合。
- 成分数默认 32，且不超过好通道数。
- 同一组权重应用到该 session 每个 trial 的 **0.5–100 Hz 整段**。

**肌电 IC**

对每个 IC 做 Welch PSD：

- LF：1–30 Hz 功率和
- HF：30–100 Hz 功率和
- \(r = HF / LF\)，再对 \(r\) 做 MAD-z
- `z > 3.0` → 显著肌电 IC

按源能量从高到低删除，数量不超过好通道数的 **20%**（向下取整）。超出的显著成分只记录，用于预警。

**眼电 IC**

只用前额极 `FP1` / `FP2` / `FPZ` 做 `find_bads_eog`（阈值 3.0）。`Fz`、`FCz`、`Cz`、`F7`、`F8` 不参与。命中后按与这些通道的相关强度，最多保留 **2** 个。

**写回**

- 剔除集合 = 肌电 IC ∪ 眼电 IC
- 从 0.5–100 Hz 的整段 trial 中减去这些成分

**肌电干扰预警**（不改变 QC 是否通过）

- 显著肌电 IC 占比 ∈ [80%, 90%) → 肌电干扰预警
- ≥ 90% → 肌电严重干扰预警

ICA 拟合或套用失败时跳过本步，继续插值和重参考。

### 3.4 坏道球面插值

对**该 trial** 的 QC 坏道做球面样条插值。不用 ICA 拟合时的坏道并集。仅 contact、已被质检豁免的前额通道不在坏道列表里，因此不会被插值。

### 3.5 平均重参考

插值之后，全体头皮导联做平均参考。

---

## 4. 流水线一览

```
median_center
    ├─ bandpass 0.5–100 Hz + notch 50 Hz     → 最终数据
    └─ bandpass 1–100 Hz   + notch 50 Hz     → 仅用于 ICA 拟合
session：通过质检的 video 段拼接
    → Infomax（排除坏道并集；肌电 ≤ 好通道数的 20%，眼电 ≤ 2）
    → 权重套回每个 trial 的 0.5–100 Hz 整段
    → 该 trial 坏道球面插值
    → average reference
```
