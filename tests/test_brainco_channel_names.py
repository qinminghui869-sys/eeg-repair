import importlib.util
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts/fix_brainco_parquet_channel_names.py"
    spec = importlib.util.spec_from_file_location("fix_brainco_parquet_channel_names", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fix = _load()


def _write(path, names, values):
    pq.write_table(
        pa.table(
            {
                "timestamp": [0.0, 0.004],
                "episode_index": pa.array([0, 0], type=pa.int64()),
                **{name: pa.array(values[i], type=pa.float32()) for i, name in enumerate(names)},
            }
        ),
        path,
    )


def test_rename_keeps_column_values_and_dry_run_does_not_write(tmp_path, capsys):
    names = [f"X{i:02d}" for i in range(32)]
    values = [[float(i), float(i) + 0.5] for i in range(32)]
    path = tmp_path / "observation.eeg.parquet"
    _write(path, names, values)
    original = path.read_bytes()
    assert fix.main([str(path), "--dry-run"]) == 0
    assert path.read_bytes() == original
    assert fix.main([str(path)]) == 0
    table = pq.read_table(path)
    assert table.column_names == ["timestamp", "episode_index", *fix.HW32]
    for index, column in enumerate(fix.HW32):
        assert table[column].to_pylist() == values[index]
    assert fix.main([str(path)]) == 0
    assert "status: OK" in capsys.readouterr().out


def test_thirty_three_channels_include_trigger_prefix(tmp_path, capsys):
    names = [f"X{i:02d}" for i in range(33)]
    values = [[float(i)] for i in range(33)]
    path = tmp_path / "eeg.parquet"
    pq.write_table(
        pa.table({name: pa.array(values[i], type=pa.float32()) for i, name in enumerate(names)}),
        path,
    )
    assert fix.main([str(path)]) == 0
    table = pq.read_table(path)
    assert table.column_names == fix.HW33
    assert table["TRIG"].to_pylist() == [0.0]
    assert table["P8"].to_pylist() == [1.0]


def test_other_channel_counts_are_rejected(tmp_path, capsys):
    path = tmp_path / "eeg.parquet"
    pq.write_table(pa.table({"timestamp": [0.0], "Fp1": [1.0]}), path)
    original = path.read_bytes()
    assert fix.main([str(path)]) == 1
    assert path.read_bytes() == original
    assert "32/33" in capsys.readouterr().err
    assert fix.main([str(tmp_path / "missing.parquet")]) == 1
