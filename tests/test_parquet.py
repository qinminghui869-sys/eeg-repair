from dataclasses import replace
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eeg_repair.config import Config, load_config
from eeg_repair.parquet import read_recording, replace_signals, publish, sha256


def fixture_file(tmp_path, *, layout="columns", unit="uV"):
    cfg = Config(
        channels=("Fp1", "Fp2", "C3", "C4", "IO"), sfreq=250, unit=unit, min_duration_s=0.1, layout=layout
    )
    data = np.arange(500, dtype=np.float32).reshape(100, 5)
    columns = {
        "timestamp": pa.array(np.arange(100) / 250),
        "episode_index": pa.array([3] * 100),
        "label": pa.array(["保留"] * 100),
    }
    if layout == "columns":
        columns.update({name: pa.array(data[:, i]) for i, name in enumerate(cfg.channels)})
    else:
        columns["eeg"] = pa.array(data.tolist(), type=pa.list_(pa.float32(), 5))
    table = pa.table(columns).replace_schema_metadata({b"custom": b"keep-me"})
    path = tmp_path / "source.parquet"
    pq.write_table(table, path, compression="zstd", row_group_size=37, use_compliant_nested_type=False)
    return cfg, path, table


@pytest.mark.parametrize("layout", ["columns", "vector"])
@pytest.mark.parametrize("unit", ["uV", "mV", "V"])
def test_roundtrip_preserves_source_schema_aux_and_time(tmp_path, layout, unit):
    cfg, path, source = fixture_file(tmp_path, layout=layout, unit=unit)
    before = sha256(path)
    record = read_recording(path, cfg)
    repaired = record.data_uv[:4] + 13
    table = replace_signals(record, repaired, cfg, [0, 1, 2, 3])
    output = tmp_path / "repair.parquet"
    publish(record, table, output)
    got = pq.read_table(output)
    assert source.schema.equals(got.schema, check_metadata=True)
    for name in ["timestamp", "episode_index", "label"]:
        assert got[name].equals(source[name])
    if layout == "columns":
        assert got["IO"].equals(source["IO"])
    else:
        np.testing.assert_array_equal(
            np.asarray(got["eeg"].to_pylist())[:, 4], np.asarray(source["eeg"].to_pylist())[:, 4]
        )
    assert sha256(path) == before
    meta = pq.ParquetFile(output).metadata
    assert [meta.row_group(i).num_rows for i in range(meta.num_row_groups)] == [37, 37, 26]
    assert meta.row_group(0).column(0).compression == "ZSTD"
    assert not output.with_suffix(".audit.json").exists()
    with pytest.raises(FileExistsError):
        publish(record, table, output)
    with pytest.raises(ValueError, match="overwritten"):
        publish(record, table, path)


def test_source_modified_during_run_rejected(tmp_path):
    cfg, path, source = fixture_file(tmp_path)
    record = read_recording(path, cfg)
    pq.write_table(source, path, compression="snappy")
    with pytest.raises(ValueError, match="source changed"):
        publish(record, source, tmp_path / "result.parquet")
    assert not (tmp_path / "result.parquet").exists()
    assert not (tmp_path / "result.audit.json").exists()


def test_nonfinite_result_rejected(tmp_path):
    cfg, path, _ = fixture_file(tmp_path)
    record = read_recording(path, cfg)
    with pytest.raises(ValueError, match="finite"):
        replace_signals(record, np.full((4, 100), np.nan), cfg, [0, 1, 2, 3])


def test_bad_timestamp_rejected(tmp_path):
    cfg, path, table = fixture_file(tmp_path)
    table = table.set_column(0, table.schema.field(0), pa.array([0.0] * 100))
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="increasing"):
        read_recording(path, cfg)


def test_config_rejects_unknown_and_unconfirmed_unit(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("channels: [Fp1]\nsfreq: 250\nunit: uV\ntypo: true\n")
    with pytest.raises(TypeError):
        load_config(path)
    with pytest.raises(ValueError):
        Config(channels=("Fp1",), sfreq=250, unit=None)
    with pytest.raises(ValueError):
        Config(channels=("Fp1",), sfreq=100, unit="uV")


def test_samplerate_mismatch(tmp_path):
    cfg, path, _ = fixture_file(tmp_path)
    with pytest.raises(ValueError, match="sampling rate"):
        read_recording(path, replace(cfg, sfreq=500))
