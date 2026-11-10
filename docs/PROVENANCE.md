# 来源与迁移记录

本项目从现有 EEG 修复工作中提取为独立工具包。

- 交互流程图：原项目流程图已迁入 `docs/eeg_repair_flowchart.html`，并按当前入口与输出契约同步说明。
- 算法参考：原预处理函数存档于 `docs/reference/`。
- 算法文档：原流程说明存档于 `docs/reference/`。
- 未迁入原模型、训练、GPU 推理、FIF 掩码重建入口及旧配置；这些不属于已确认的修复主干。

接入检查时，样例数据用于核对通道布局、采样率与字段类型；单位与实测电极坐标通常不在元数据中声明，配置模板将单位置空，需采集方确认。不根据数据幅度猜测单位，也不提供未经来源确认的电极坐标。

新代码保留输入输出约束并增加检查、审计与测试。参考材料授权范围未知，未擅自添加开源许可证。

## 暂定坐标资产

暂定坐标仅用于缺少完整实测坐标时的试运行；单位须按批次确认，确认结果不外推。

- 原始资产：`configs/montages/neuroscan_net32_pan_mm.csv`，抄录 Neuroscan CURRY Helpdesk 公开的 32 导 PAN 坐标（mm）：https://sccn.ucsd.edu/pipermail/eeglablist/2022/016586.html 。
- `Cb1/Cb2/Cbz` 按 CURRY 官方标签匹配规则对应 `I1/I2/Iz`：https://www.compumedicsneuroscan.com/Uploads/Curry7012ReleaseNotes.pdf 第 13 页。
- 生成脚本：`scripts/build_provisional_montage.py`。基础模板为 MNE `colin27_1005`（原名 `standard_1005`），用模板基准点转换至 head 坐标后，与共同电极作无反射相似配准（排除 M1/M2）。该变换只用于把模板与 PAN 源对齐并记录残差，不写回 `F11/F12/FT11/FT12`。
- `F11/F12/FT11/FT12` 直接采用上述邮件中的 PAN xyz，由毫米换算为米，不经过相似变换。因此这 4 个点与其余已转入 MNE head 的电极不在同一套变换中。
- 输出：`configs/montages/provisional_full.csv` 及同名 `.registration.json`，记录源哈希、生成物哈希、MNE 版本、变换矩阵、共同电极残差，以及这 4 个通道的公布坐标。完整 CSV 导出正式设备配置中头皮通道的并集。
- 试运行配置 `configs/provisional.yaml` 显式选择该资产；正式配置仍保持原限制。审计保存配准报告并验证 CSV 哈希。来源明确、程序运行成功和坐标科学验证是不同层次的证据。
