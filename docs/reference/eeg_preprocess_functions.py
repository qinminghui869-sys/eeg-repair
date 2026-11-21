"""迁入前的 BrainCo 预处理函数存档，不是本仓库的执行入口。

当前行为见 README 与 docs/ALGORITHM.md。

顺序：去直流 → 带通+陷波 → 标坏道 → 同一 session 的干净片段拼接后做一次 ICA
（肌电+眼电）→ 坏道插值 → 平均参考。
输入 ``data_uv`` 形状为 ``[通道, 时间]``，单位 µV。
"""

from __future__ import annotations

from pathlib import Path

import mne
import numpy as np
from scipy.signal import butter, sosfiltfilt, welch

AUX = frozenset({"VEOG", "HEOG", "EKG", "EMG", "TRIGGER", "TRIG", "IO"})
CONTACT_EXEMPT = frozenset({"FP1", "FP2", "FPZ", "AF3", "AF4"})
EOG_PROXY = ("FP1", "FP2", "FPZ")
MAX_EOG_ICS = 2
PASS_BAD_RATE = 0.30
ICA_FIT_L_FREQ = 1.0
MONTAGE_CSV = Path(__file__).resolve().parent / "brainco_32_montage.csv"


def _mad(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.nan
    med = float(np.median(x))
    return float(1.4826 * np.median(np.abs(x - med)))


def _up(name: str) -> str:
    return str(name).strip().upper()


def scalp_names(names: list[str]) -> tuple[list[int], list[str]]:
    idx, keep = [], []
    for i, name in enumerate(names):
        if _up(name) not in AUX:
            idx.append(i)
            keep.append(str(name))
    return idx, keep


def load_montage(csv_path: Path | str = MONTAGE_CSV) -> mne.channels.DigMontage:
    import pandas as pd

    df = pd.read_csv(csv_path)
    pos = {
        str(r["name"]): np.array([r["x_m_mne"], r["y_m_mne"], r["z_m_mne"]], float)
        for _, r in df.iterrows()
    }
    return mne.channels.make_dig_montage(ch_pos=pos, coord_frame="head")


def _montage_pos(montage) -> dict[str, np.ndarray]:
    if montage is None:
        return {}
    got = montage.get_positions() if hasattr(montage, "get_positions") else {}
    ch_pos = got.get("ch_pos", {}) if isinstance(got, dict) else {}
    return {_up(k): np.asarray(v, float) for k, v in ch_pos.items()}


def _raw(data_uv, names, sfreq, montage=None, bads=None) -> mne.io.RawArray:
    raw = mne.io.RawArray(
        np.asarray(data_uv, np.float64) * 1e-6,
        mne.create_info(list(names), sfreq, "eeg"),
        verbose=False,
    )
    if montage is not None:
        try:
            raw.set_montage(montage, on_missing="ignore")
        except TypeError:
            raw.set_montage(montage)
    if bads:
        raw.info["bads"] = [n for n in bads if n in raw.ch_names]
    return raw


def remove_dc(data_uv: np.ndarray) -> np.ndarray:
    """各通道减去时间中位数。"""
    x = np.asarray(data_uv, np.float32)
    return x - np.nanmedian(x, axis=1, keepdims=True)


def bandpass_notch(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    *,
    l_freq: float = 0.5,
    h_freq: float = 100.0,
    notch_hz: float | list[float] = 50.0,
) -> np.ndarray:
    """FIR 带通，再陷波。高频不超过 Nyquist−1 Hz。"""
    h = min(float(h_freq), sfreq / 2.0 - 1.0)
    freqs = [float(notch_hz)] if isinstance(notch_hz, (int, float)) else [float(f) for f in notch_hz]
    raw = _raw(data_uv, ch_names, sfreq)
    raw.filter(l_freq, h, picks="eeg", verbose=False)
    raw.notch_filter(freqs, picks="eeg", verbose=False)
    return (raw.get_data(picks="eeg") * 1e6).astype(np.float32)


def _noise_index(data_uv: np.ndarray, sfreq: float, split_hz: float = 50.0) -> np.ndarray:
    if data_uv.shape[1] < 8 or sfreq / 2.0 <= split_hz:
        return np.full(data_uv.shape[0], np.nan)
    cutoff = split_hz / (sfreq / 2.0)
    low = sosfiltfilt(butter(4, cutoff, btype="low", output="sos"), data_uv, axis=1)
    high = sosfiltfilt(butter(4, cutoff, btype="high", output="sos"), data_uv, axis=1)
    out = np.full(data_uv.shape[0], np.nan)
    for i in range(data_uv.shape[0]):
        ml, mh = _mad(low[i]), _mad(high[i])
        if np.isfinite(ml) and ml >= 1e-12:
            out[i] = mh / ml
    return out


def _bridges(data_uv: np.ndarray, names: list[str], sfreq: float, montage) -> set[str]:
    """近邻电极电学距离过小 → 桥接。短于 10 s 则跳过。"""
    n_ch, n_t = data_uv.shape
    if n_t / sfreq < 10.0 or n_ch < 2:
        return set()
    win = max(1, int(round(2.0 * sfreq)))
    n_ep = n_t // win
    if n_ep < 1:
        return set()
    ed = np.full((n_ep, n_ch, n_ch), np.nan)
    for e in range(n_ep):
        seg = data_uv[:, e * win : (e + 1) * win].astype(np.float64)
        seg -= seg.mean(axis=1, keepdims=True)
        cov = np.cov(seg)
        d = np.diag(cov)
        mat = d[:, None] + d[None, :] - 2.0 * cov
        np.fill_diagonal(mat, np.nan)
        ed[e] = mat
    med = np.nanmedian(ed, axis=0)
    pos = _montage_pos(montage)
    xyz = [pos.get(_up(n)) for n in names]
    neighbors: list[set[int]] = []
    if all(p is not None for p in xyz):
        p = np.stack(xyz)
        dist = np.linalg.norm(p[:, None, :] - p[None, :, :], axis=-1)
        np.fill_diagonal(dist, np.inf)
        neighbors = [set(np.argsort(dist[i])[:4].tolist()) for i in range(n_ch)]
    else:
        neighbors = []
        for i in range(n_ch):
            row = med[i].copy()
            row[i] = np.inf
            neighbors.append(set(int(j) for j in np.argsort(row)[:4] if np.isfinite(row[j])))
    bad: set[str] = set()
    for i in range(n_ch):
        for j in sorted(neighbors[i]):
            if j <= i or i not in neighbors[j]:
                continue
            vals = ed[:, i, j]
            vals = vals[np.isfinite(vals)]
            if vals.size == 0 or float(np.mean(vals < 16.0)) < 0.5:
                continue
            m = float(med[i, j])
            if not np.isfinite(m) or m >= 16.0:
                continue
            other_i = [float(med[i, k]) for k in neighbors[i] if k != j and np.isfinite(med[i, k])]
            other_j = [float(med[j, k]) for k in neighbors[j] if k != i and np.isfinite(med[j, k])]
            if not other_i or not other_j:
                continue
            ref = min(float(np.median(other_i)), float(np.median(other_j)))
            if ref > 0 and m <= 0.5 * ref:
                bad.add(names[i])
                bad.add(names[j])
    return bad


def _is_bad(name: str, flat: bool, contact: bool, bridge: bool, corr: bool, hf: bool) -> bool:
    """仅前额 contact、或单独低相关，不计入坏道。"""
    other = flat or contact or bridge or hf
    exempt = contact and not (flat or bridge or corr or hf) and _up(name) in CONTACT_EXEMPT
    return bool(flat or bridge or hf or (contact and not exempt) or (corr and other))


def mark_bad_channels(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    montage: mne.channels.DigMontage | None = None,
) -> dict:
    """五规则标坏道。返回坏道名单；坏道率 < 0.30 为通过。"""
    n_ch = data_uv.shape[0]
    std = np.nanstd(data_uv, axis=1)
    diff = np.abs(np.diff(data_uv, axis=1))
    flat_ratio = np.mean(diff < 0.02, axis=1) if diff.shape[1] else np.zeros(n_ch)
    flat = (std < 0.2) & (flat_ratio > 0.7)
    z = (std - np.nanmedian(std)) / (_mad(std) + 1e-12)
    contact = (z > 3.5) | ((z > 3.0) & (std > 25.0))
    bridged = _bridges(data_uv, ch_names, sfreq, montage)
    bridge = np.array([n in bridged for n in ch_names])
    corr = _low_correlation(data_uv, sfreq, exclude=flat)
    hf = _hf_noise(data_uv, sfreq, exclude=flat)
    flags = [
        _is_bad(ch_names[i], bool(flat[i]), bool(contact[i]), bool(bridge[i]), bool(corr[i]), bool(hf[i]))
        for i in range(n_ch)
    ]
    bad = [ch_names[i] for i, hit in enumerate(flags) if hit]
    rate = len(bad) / max(n_ch, 1)
    return {"bad_channels": bad, "bad_rate": rate, "passed": rate < PASS_BAD_RATE}


def _low_correlation(data_uv, sfreq, exclude) -> np.ndarray:
    n_ch, n_t = data_uv.shape
    win = max(1, int(round(sfreq)))
    n_win = n_t // win
    bad = np.zeros(n_ch, dtype=bool)
    if n_win < 4:
        return bad
    usable = ~np.asarray(exclude, bool)
    low = np.zeros(n_ch)
    seen = np.zeros(n_ch)
    for w in range(n_win):
        seg = data_uv[:, w * win : (w + 1) * win]
        seg = seg - np.nanmean(seg, axis=1, keepdims=True)
        std = np.nanstd(seg, axis=1)
        idx = np.where(usable & np.isfinite(std) & (std > 1e-12))[0]
        if idx.size < 2:
            continue
        c = np.corrcoef(seg[idx] / std[idx, None])
        np.fill_diagonal(c, np.nan)
        row = np.nanmax(np.abs(c), axis=1)
        for k, i in enumerate(idx):
            if np.isfinite(row[k]):
                low[i] += float(row[k] < 0.4)
                seen[i] += 1
    for i in range(n_ch):
        if usable[i] and seen[i] and low[i] / seen[i] > 0.20:
            bad[i] = True
    return bad


def _hf_noise(data_uv, sfreq, exclude) -> np.ndarray:
    idx = _noise_index(data_uv, sfreq, 50.0)
    usable = np.isfinite(idx) & ~np.asarray(exclude, bool)
    bad = np.zeros(data_uv.shape[0], dtype=bool)
    if int(usable.sum()) < 3:
        return bad
    z = (idx - np.nanmedian(idx[usable])) / (_mad(idx[usable]) + 1e-12)
    bad[usable] = z[usable] > 5.0
    return bad


def _muscle_ics(ica, raw, sfreq, z_thresh=3.0, max_ics=0) -> tuple[list[int], list[int]]:
    src = np.asarray(ica.get_sources(raw).get_data())
    if src.ndim == 3:
        src = src[0]
    nper = min(src.shape[1], 4096)
    freqs, psd = welch(src, fs=sfreq, axis=1, nperseg=nper, noverlap=nper // 2, detrend=False)
    ratio = psd[:, (freqs >= 30) & (freqs <= 100)].sum(1) / np.maximum(psd[:, (freqs >= 1) & (freqs < 30)].sum(1), 1e-12)
    z = (ratio - np.nanmedian(ratio)) / (_mad(ratio) + 1e-12)
    sig = [int(i) for i in np.where(z > z_thresh)[0]]
    energy = np.sum(src.astype(np.float64) ** 2, axis=1)
    ranked = sorted(sig, key=lambda i: float(energy[i]), reverse=True)
    return sig, sorted(ranked[:max_ics])


def _eog_ics(ica, raw) -> list[int]:
    have = {_up(n): n for n in raw.ch_names}
    proxy = [have[p] for p in EOG_PROXY if p in have and have[p] not in set(raw.info["bads"])]
    if not proxy:
        return []
    try:
        idx, scores = ica.find_bads_eog(raw, ch_name=proxy, threshold=3.0)
    except Exception:
        return []
    detected = [int(i) for i in (idx or [])]
    arr = None if scores is None else np.asarray(scores, dtype=float)
    strength: dict[int, float] = {}
    for i in detected:
        if arr is None:
            strength[i] = 0.0
        elif arr.ndim == 1 and i < arr.shape[0]:
            strength[i] = abs(float(arr[i]))
        elif arr.ndim >= 2 and i < arr.shape[-1]:
            strength[i] = float(np.nanmax(np.abs(arr[..., i])))
        else:
            strength[i] = 0.0
    kept = sorted(strength, key=lambda i: strength[i], reverse=True)[:MAX_EOG_ICS]
    return sorted(kept)


def fit_ica(
    segments: list[np.ndarray],
    ch_names: list[str],
    sfreq: float,
    bad_channels: list[str],
    montage: mne.channels.DigMontage | None = None,
    *,
    n_components: int = 32,
) -> tuple[mne.preprocessing.ICA, list[str], dict]:
    """把干净片段沿时间拼接后拟合一次。坏道不进入拟合。"""
    fit = np.concatenate([np.asarray(s, np.float32) for s in segments], axis=1)
    bads = [n for n in bad_channels if n in ch_names]
    n_good = max(1, len(ch_names) - len(bads))
    n_comp = max(1, min(n_components, n_good, fit.shape[0], fit.shape[1] - 1))
    raw_fit = _raw(fit, ch_names, sfreq, montage, bads)
    ica = mne.preprocessing.ICA(n_components=n_comp, method="infomax", random_state=97, max_iter=500)
    ica.fit(raw_fit)
    sig, muscle = _muscle_ics(ica, raw_fit, sfreq, max_ics=int(n_good * 0.20))
    eog = _eog_ics(ica, raw_fit)
    ica.exclude = sorted(set(muscle) | set(eog))
    info = {
        "n_components": n_comp,
        "muscle_ics": muscle,
        "muscle_significant": sig,
        "eog_ics": eog,
        "excluded": list(ica.exclude),
        "fit_duration_s": float(fit.shape[1]) / float(sfreq),
        "n_segments": len(segments),
        "exclude_channels": bads,
    }
    return ica, bads, info


def apply_ica(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    ica: mne.preprocessing.ICA,
    bad_channels: list[str],
    montage: mne.channels.DigMontage | None = None,
) -> np.ndarray:
    """用已拟合 ICA 清洗整段。``bad_channels`` 须与拟合时相同。"""
    raw = _raw(data_uv, ch_names, sfreq, montage, bad_channels)
    return (ica.apply(raw.copy()).get_data(picks="eeg") * 1e6).astype(np.float32)


def interpolate_bads(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    bad_channels: list[str],
    montage: mne.channels.DigMontage,
) -> np.ndarray:
    """球面样条重建坏道。"""
    bads = [n for n in bad_channels if n in ch_names]
    if not bads:
        return np.asarray(data_uv, np.float32)
    raw = _raw(data_uv, ch_names, sfreq, montage, bads)
    raw.interpolate_bads(reset_bads=True, verbose=False)
    return (raw.get_data(picks="eeg") * 1e6).astype(np.float32)


def average_reference(
    data_uv: np.ndarray,
    ch_names: list[str],
    sfreq: float,
    montage: mne.channels.DigMontage | None = None,
) -> np.ndarray:
    raw = _raw(data_uv, ch_names, sfreq, montage)
    try:
        raw.set_eeg_reference("average", projection=False, verbose=False)
    except TypeError:
        raw.set_eeg_reference("average", projection=False)
    return (raw.get_data(picks="eeg") * 1e6).astype(np.float32)


def _prepare(data_uv, ch_names, sfreq, montage, l_freq, h_freq, notch_hz, window):
    idx, names = scalp_names(ch_names)
    x = bandpass_notch(
        remove_dc(np.asarray(data_uv, np.float32)[idx]),
        names,
        sfreq,
        l_freq=l_freq,
        h_freq=h_freq,
        notch_hz=notch_hz,
    )
    qc_x = x if window is None else x[:, int(window[0]) : int(window[1])]
    qc = mark_bad_channels(qc_x if qc_x.shape[1] > 8 else x, names, sfreq, montage)
    return x, names, qc


def preprocess_session(
    trials: list[dict],
    sfreq: float,
    *,
    montage: mne.channels.DigMontage | None = None,
    do_ica: bool = True,
    l_freq: float = 0.5,
    h_freq: float = 100.0,
    notch_hz: float = 50.0,
) -> list[dict]:
    """同一 session 的多个 trial。

    最终数据按 ``l_freq``–``h_freq``（默认 0.5–100 Hz）滤波。
    ICA 在 1–``h_freq`` 的拷贝上拟合，再把权重套回最终数据。
    """
    montage = montage or load_montage()
    fit_l = max(float(l_freq), float(ICA_FIT_L_FREQ))
    prepared = []
    for trial in trials:
        x, names, qc = _prepare(
            trial["data_uv"],
            trial["ch_names"],
            sfreq,
            montage,
            l_freq,
            h_freq,
            notch_hz,
            trial.get("window"),
        )
        x_fit = x
        if fit_l > float(l_freq) + 1e-9:
            x_fit, _, _ = _prepare(
                trial["data_uv"],
                trial["ch_names"],
                sfreq,
                montage,
                fit_l,
                h_freq,
                notch_hz,
                None,
            )
        prepared.append({
            "x": x,
            "x_fit": x_fit,
            "names": names,
            "qc": qc,
            "window": trial.get("window"),
            "passed": trial.get("passed", True),
        })
    ica = info = bads = None
    if do_ica and prepared:
        names = prepared[0]["names"]
        chosen = [p for p in prepared if p["passed"]] or prepared
        segs = []
        union: list[str] = []
        for p in chosen:
            w = p["window"]
            src = p["x_fit"]
            seg = src if w is None else src[:, int(w[0]) : int(w[1])]
            if seg.shape[1] < sfreq:
                continue
            segs.append(seg)
            for ch in p["qc"]["bad_channels"]:
                if ch not in union:
                    union.append(ch)
        if segs and sum(s.shape[1] for s in segs) / sfreq >= 5.0:
            try:
                ica, bads, info = fit_ica(segs, names, sfreq, union, montage)
                if info is not None:
                    info["ica_fit_l_freq"] = fit_l
                    info["ica_apply_l_freq"] = float(l_freq)
            except Exception as exc:
                info = {"error": f"{type(exc).__name__}: {exc}", "excluded": []}
    out = []
    for p in prepared:
        x = p["x"]
        if ica is not None:
            x = apply_ica(x, p["names"], sfreq, ica, bads or [], montage)
        x = interpolate_bads(x, p["names"], sfreq, p["qc"]["bad_channels"], montage)
        x = average_reference(x, p["names"], sfreq, montage)
        out.append({"data_uv": x, "ch_names": p["names"], "sfreq": float(sfreq), "qc": p["qc"], "ica": info or {"excluded": []}})
    return out
