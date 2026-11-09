"""Strict, explicit configuration; units and channel order are never guessed."""

from dataclasses import asdict, dataclass
from pathlib import Path
import math
import yaml


@dataclass(frozen=True)
class Config:
    channels: tuple[str, ...]
    sfreq: float
    unit: str
    device: str | None = None
    metadata_path: str | None = None
    layout: str = "columns"
    signal_column: str = "eeg"
    highpass_hz: float = 0.5
    lowpass_hz: float = 100.0
    notch_hz: tuple[float, ...] = (50.0, 100.0)
    montage: str = "standard_1020"
    montage_csv: str | None = None
    ica_enabled: bool = True
    ica_components: int = 32
    min_duration_s: float = 10.0
    max_bad_ratio: float = 0.4
    impedance_threshold_kohm: float = 150.0
    impedance_unit: str = "ohm"

    def __post_init__(self):
        if (
            not self.channels
            or len(self.channels) > 256
            or any(not isinstance(n, str) or not n.strip() for n in self.channels)
        ):
            raise ValueError("channels must contain 1–256 nonempty names")
        if len({n.upper() for n in self.channels}) != len(self.channels):
            raise ValueError("channel names must be unique (case-insensitive)")
        if self.layout not in {"columns", "vector"} or self.unit not in {"uV", "mV", "V"}:
            raise ValueError("layout must be columns/vector; unit must be uV/mV/V")
        numeric = (
            self.sfreq,
            self.highpass_hz,
            self.lowpass_hz,
            self.min_duration_s,
            self.max_bad_ratio,
            self.impedance_threshold_kohm,
            *self.notch_hz,
        )
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numeric
        ):
            raise ValueError("numeric settings must be finite numbers")
        if not (self.sfreq >= 100 and 0 < self.highpass_hz < self.lowpass_hz < self.sfreq / 2):
            raise ValueError("require sfreq >= 100 and 0 < highpass < lowpass < Nyquist")
        if self.min_duration_s <= 0 or not 0 <= self.max_bad_ratio < 1 or self.impedance_threshold_kohm <= 0:
            raise ValueError("invalid duration, bad-channel ratio, or impedance threshold")
        if (
            type(self.ica_enabled) is not bool
            or type(self.ica_components) is not int
            or self.ica_components < 2
        ):
            raise ValueError("ICA enable must be boolean; components must be an integer >= 2")
        if self.impedance_unit not in {"ohm", "kohm"} or any(f <= 0 for f in self.notch_hz):
            raise ValueError("invalid impedance unit or notch frequency")

    def to_dict(self):
        return asdict(self)


def load_config(path: str | Path | None = None, *, source: str | Path | None = None) -> Config:
    path = Path(path) if path is not None else None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path else {}
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    if source is not None:
        from .metadata import read_metadata

        metadata = read_metadata(source)
        for key in ("channels", "sfreq", "unit", "device"):
            value = metadata.get(key)
            if value is not None:
                if data.get(key) is not None and data[key] != value:
                    raise ValueError(f"{key} conflicts with recording metadata")
                data[key] = value
        if metadata:
            data["metadata_path"] = metadata["metadata_path"]
            data.setdefault("montage", "standard_1005")
            if metadata.get("shape") != [len(data.get("channels") or [])]:
                raise ValueError("metadata shape does not match channel names")
    missing = [key for key in ("channels", "sfreq", "unit") if data.get(key) is None]
    if missing:
        raise ValueError(f"missing recording parameters: {', '.join(missing)}; supply explicit config")
    for name in ("channels", "notch_hz"):
        if name in data:
            if not isinstance(data[name], list):
                raise ValueError(f"{name} must be a list")
            data[name] = tuple(data[name])
    if data.get("montage_csv"):
        data["montage_csv"] = str(((path.parent if path else Path.cwd()) / data["montage_csv"]).resolve())
    return Config(**data)
