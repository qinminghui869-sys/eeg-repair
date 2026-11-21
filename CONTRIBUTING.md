# 开发约定

- 信号算法放在 `algorithms.py`，处理顺序放在 `pipeline.py`，存储契约放在 `parquet.py`。不要在 CLI 里复制算法。
- 输入数据只读。测试使用临时目录。不提交真实脑电、`outputs/` 或本机环境。
- 同一事实只写一处：操作参数写 README，实现差异写 `docs/ALGORITHM.md`，坐标来源写 `docs/PROVENANCE.md`，测试范围写 `docs/VALIDATION.md`。改阈值、单位、通道映射、滤波顺序或质量分档时，同步 README 参数表并说明依据。不要放宽门控来掩盖错误。
- 配置拒绝未知字段。新增配置要有默认值，或有明确的必填检查。
- 改 schema、辅助通道、时间轴或输出路径时保留回归测试。
- 提交前运行 `ruff check src tests`、`ruff format --check src tests`、`pytest -q`。CI 在 Python 3.11 和 3.12 上执行相同检查。
- 评审说明包含问题、行为变化、验证结果和未验证的数据范围。0.1.0 是内部初始版本，不代表生产数据已经验收。
