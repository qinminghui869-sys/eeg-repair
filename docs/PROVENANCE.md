# 来源与迁移记录

本项目从现有 EEG 修复工作中提取，原项目及用户上传文件均保留。

- 交互流程图：`/home/baai/qmh/zuna/docs/eeg_repair_flowchart.html`。新副本继续保留节点 I 的蓝色样式，并按新入口和输出契约同步说明。
- 算法参考：`/home/baai/Downloads/eeg_preprocess_functions.py`，与原项目 `src/zuna/preprocessing/eeg_preprocess_functions.py` 内容一致。原件存档于 `docs/reference/`。
- 算法文档：`/home/baai/Downloads/EEG_PREPROCESS_PIPELINE.md`，原件存档于 `docs/reference/`。
- 未迁入原 ZUNA 模型、训练、GPU 推理、FIF 掩码重建入口及旧配置；这些不属于已确认的修复主干。

数据样本（只读检查，未复制进入项目）：

- `L3-003-2026-11-04-17-57-55`：44127 行，31 头皮通道 + IO/TRIG；`meta/info.json` 记录 249.7 Hz。
- `L3-001-2026-09-21-10-14-15`：31350 行，128 通道 + VEOG/HEOG/EKG/EMG；`meta/info.json` 记录 1000 Hz。
- 两者 EEG 字段为 float32，timestamp 为 double，episode_index 为 int64。

检索了两目录内文件清单及 `meta/*.json{,l}`，未找到单位声明、实测电极坐标文件或阻抗 Parquet。数据配置模板将单位置空，需采集方确认；不根据数据幅度猜测单位。不提供未经来源确认的电极坐标。

新代码保留输入输出约束并增加检查、审计与测试。参考材料授权范围未知，未擅自添加开源许可证，也未发布到外部仓库。
