# 开发约定

- 信号算法放在 `algorithms.py`，处理顺序放在 `pipeline.py`，存储契约放在 `parquet.py`。不要在 CLI 中复制算法。
- 所有输入数据只读，测试使用临时目录；不提交真实脑电数据、`outputs/` 或本机环境。本地运行使用 `scripts/repair.sh`。
- 改动阈值、单位换算、通道映射、滤波顺序或质量分档时，同步 README 参数表和 `docs/ALGORITHM.md`，并说明依据。不要用更宽松的门控隐藏错误。
- 配置拒绝未知字段；新增配置应有默认值或明确的必填检查。
- 涉及 schema、辅助通道、时间轴或输出路径的修改必须保留回归测试。
- 提交前运行 `ruff check src tests`、`ruff format --check src tests`、`pytest -q`。CI 在 Python 3.11 / 3.12 上执行相同检查。
- 评审说明应包含问题、行为变化、验证结果和未验证的数据范围。版本 0.1.0 为内部初始整理版本，不代表已完成生产数据质量验收。
