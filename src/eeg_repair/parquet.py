"""Parquet boundary: preserve schema, untouched columns and row-group sizes."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .config import Config


@dataclass
class Recording:
    path: Path
    table: pa.Table
    data_uv: np.ndarray
    row_groups: list[int]
    compression: dict[str, str]
    source_hash: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_recording(path: str | Path, cfg: Config) -> Recording:
    path = Path(path).resolve()
    source_hash = sha256(path)
    parquet = pq.ParquetFile(path)
    table = parquet.read()
    if table.num_rows / cfg.sfreq < cfg.min_duration_s:
        raise ValueError("recording is shorter than min_duration_s")
    if len(table.column_names) != len(set(table.column_names)):
        raise ValueError("duplicate Parquet column names")
    if cfg.layout == "columns":
        arrays = []
        for name in cfg.channels:
            col = table[name]
            if not pa.types.is_floating(col.type) or col.null_count:
                raise ValueError(f"{name}: require non-null floating point EEG data")
            arrays.append(col.to_numpy())
        data = np.stack(arrays)
    else:
        col = table[cfg.signal_column]
        if not (
            pa.types.is_list(col.type)
            or pa.types.is_large_list(col.type)
            or pa.types.is_fixed_size_list(col.type)
        ):
            raise ValueError("signal_column must contain lists of channel samples")
        if not pa.types.is_floating(col.type.value_type) or col.null_count:
            raise ValueError("vector samples must be non-null floating point data")
        data = np.asarray(col.to_pylist(), dtype=np.float64).T
    if data.shape != (len(cfg.channels), table.num_rows) or not np.isfinite(data).all():
        raise ValueError("invalid EEG shape or NaN/Inf")
    if "timestamp" not in table.column_names or not pa.types.is_float64(table["timestamp"].type):
        raise ValueError("timestamp column is required and must be float64 seconds")
    stamps = table["timestamp"].to_numpy()
    if (
        table["timestamp"].null_count
        or not np.isfinite(stamps).all()
        or np.any(stamps < 0)
        or not np.all(np.diff(stamps) > 0)
    ):
        raise ValueError("timestamps must be finite, non-negative, and strictly increasing")
    observed = 1.0 / float(np.median(np.diff(stamps)))
    if abs(observed / cfg.sfreq - 1) > 0.005:
        raise ValueError(f"timestamp sampling rate {observed:.3f} differs from configured {cfg.sfreq}")
    if "episode_index" not in table.column_names or not pa.types.is_int64(table["episode_index"].type):
        raise ValueError("episode_index column is required and must be int64")
    episodes = table["episode_index"]
    if episodes.null_count or len(episodes.unique().to_pylist()) != 1:
        raise ValueError("episode_index must be non-null and constant within a file")
    compression = {}
    for g in range(parquet.num_row_groups):
        group = parquet.metadata.row_group(g)
        for i in range(group.num_columns):
            col = group.column(i)
            codec = col.compression.lower().replace("uncompressed", "none")
            if col.path_in_schema in compression and compression[col.path_in_schema] != codec:
                raise ValueError("mixed per-row-group compression is not supported")
            compression[col.path_in_schema] = codec
    if sha256(path) != source_hash:
        raise ValueError("source changed during read")
    scale = {"uV": 1.0, "mV": 1000.0, "V": 1e6}[cfg.unit]
    return Recording(
        path,
        table,
        data.astype(np.float64) * scale,
        [parquet.metadata.row_group(i).num_rows for i in range(parquet.num_row_groups)],
        compression,
        source_hash,
    )


def replace_signals(record: Recording, repaired_uv: np.ndarray, cfg: Config, scalp: list[int]) -> pa.Table:
    if repaired_uv.shape != (len(scalp), record.table.num_rows) or not np.isfinite(repaired_uv).all():
        raise ValueError("repair result must have original shape and finite values")
    scale = {"uV": 1.0, "mV": 1000.0, "V": 1e6}[cfg.unit]
    table = record.table
    if cfg.layout == "columns":
        for row, channel_index in enumerate(scalp):
            name = cfg.channels[channel_index]
            index = table.schema.get_field_index(name)
            field = table.schema.field(index)
            values = pa.array(repaired_uv[row] / scale, type=field.type)
            if not np.isfinite(values.to_numpy()).all():
                raise ValueError("repair values overflow source storage type")
            table = table.set_column(index, field, values)
    else:
        index = table.schema.get_field_index(cfg.signal_column)
        field = table.schema.field(index)
        # Start from source values to keep auxiliary channels bit-for-bit unchanged.
        data = np.asarray(table[cfg.signal_column].to_pylist(), dtype=np.float64)
        data[:, scalp] = repaired_uv.T / scale
        values = pa.array(data.tolist(), type=field.type)
        if not np.isfinite(np.asarray(values.to_pylist())).all():
            raise ValueError("repair values overflow source storage type")
        table = table.set_column(index, field, values)
    if not table.schema.equals(record.table.schema, check_metadata=True):
        raise ValueError("source schema changed")
    return table


def publish(
    record: Recording,
    table: pa.Table,
    destination: str | Path,
    audit: dict,
    *,
    audit_path: str | Path | None = None,
) -> Path:
    """Verify temporary files before atomic no-clobber publication (local filesystem)."""
    destination = Path(destination).resolve()
    audit_path = (
        Path(audit_path).resolve() if audit_path is not None else destination.with_suffix(".audit.json")
    )
    if destination == record.path or audit_path == record.path:
        raise ValueError("source must never be overwritten")
    if destination.exists() or audit_path.exists():
        raise FileExistsError(f"output or audit already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".eeg-repair-", suffix=".parquet", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    audit_temp = temporary.with_suffix(".json")
    audit_published = False
    try:
        with pq.ParquetWriter(
            temporary, table.schema, compression=record.compression, use_compliant_nested_type=False
        ) as writer:
            offset = 0
            for size in record.row_groups:
                writer.write_table(table.slice(offset, size), row_group_size=size)
                offset += size
        verified = pq.read_table(temporary)
        if not verified.schema.equals(table.schema, check_metadata=True) or not verified.equals(table):
            raise ValueError("Parquet read-back verification failed")
        if sha256(record.path) != record.source_hash:
            raise ValueError("source changed during processing")
        audit = {
            **audit,
            "source_sha256": record.source_hash,
            "output_sha256": sha256(temporary),
            "source": str(record.path),
            "output": str(destination),
        }
        audit_temp.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        os.link(audit_temp, audit_path)
        audit_published = True
        os.link(temporary, destination)  # Atomic; fails if destination already exists.
    except BaseException:
        if audit_published:
            audit_path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        audit_temp.unlink(missing_ok=True)
    return destination
