# 坐标来源

本仓库从既有 EEG 修复流程抽出，只保留 Parquet 修复主干。原模型、训练、GPU 推理、FIF 掩码重建和旧配置没有迁入。`docs/reference/` 与 `docs/eeg_repair_flowchart.html` 是当时的流程存档，并已按当前入口改过说明。参考材料的授权范围不明，仓库未另加开源许可证。

单位和实测电极坐标通常不在元数据里。程序不根据幅度猜测单位，也不补未经确认的坐标。

## 暂定坐标

`configs/montages/provisional_full.csv` 只用于显式试运行。它导出 `configs/neuroscan_128.yaml` 的头皮通道。确认过的单位不外推到其他批次。

- 原始抄录：`configs/montages/neuroscan_net32_pan_mm.csv`，来自 Neuroscan CURRY Helpdesk 公布的 32 导 PAN 坐标（mm）：https://sccn.ucsd.edu/pipermail/eeglablist/2022/016586.html
- `Cb1/Cb2/Cbz` 按 CURRY 标签规则对应模板的 `I1/I2/Iz`：https://www.compumedicsneuroscan.com/Uploads/Curry7012ReleaseNotes.pdf 第 13 页。
- 生成脚本：`scripts/build_provisional_montage.py`。基础模板是 MNE `colin27_1005`（原名 `standard_1005`）。脚本用共同电极做无反射相似配准（排除 M1/M2），只为对齐模板与 PAN 源并记录残差，不改写 `F11/F12/FT11/FT12`。
- `F11/F12/FT11/FT12` 直接采用上述邮件中的 PAN xyz，由毫米换算为米，不经过相似变换。这 4 个点与已转入 MNE head 的电极不在同一套变换里。
- 同目录的 `provisional_full.registration.json` 记录源哈希、生成物哈希、MNE 版本、变换矩阵、共同电极残差，以及这 4 个通道的公布坐标。`configs/provisional.yaml` 显式选用该表。修复前校验坐标 CSV 哈希。

来源可追溯、程序能跑完、坐标经过科学验证，是三件不同的事。
