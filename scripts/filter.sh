#!/usr/bin/env bash
# Preflight, bandpass and notch, then structural QC.
# Dataset mode writes observation.eeg_denoise.parquet beside the source.
# The original observation.eeg.parquet is never overwritten.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/filter.sh INPUT [options]

Run preflight, bandpass and notch, and structural QC for one EEG Parquet
file, one dataset directory, or a batch directory.

This does not remove the per-channel median, and it does not write a
quality grade.

Options:
  --config FILE       Optional algorithm settings or missing metadata
  --output-dir DIR    Logs, and results for loose Parquet files
                      Default: <repo>/outputs/run_XXXXXX
  --pattern GLOB      Input filename when INPUT is a directory
                      Default: observation.eeg.parquet
  -h, --help          Show this help

Exit status is preflight, otherwise filtering, otherwise QC.
Logs: <output-dir>/logs/preflight.log, filter.log, output_qc.json, status.txt

Dataset mode writes observation.eeg_denoise.parquet back into the source
dataset and adds features.observation.eeg_denoise to meta/info.json.
--output-dir then receives logs only. Loose Parquet results use the same
filename under the mirrored relative directory in --output-dir.
EOF
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT" || exit 2
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate || exit 2
fi
export MNE_DONTWRITE_HOME=true

if ! command -v eeg-filter >/dev/null 2>&1 || ! command -v eeg-repair-qc >/dev/null 2>&1; then
  printf 'eeg-filter is not on PATH. From %s run: uv sync --locked --extra dev --python 3.11 && source .venv/bin/activate\n' "$ROOT" >&2
  exit 2
fi

INPUT=""
CONFIG=""
OUTPUT_DIR=""
PATTERN="observation.eeg.parquet"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --config)
      CONFIG=${2:?--config requires a file}
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR=${2:?--output-dir requires a directory}
      shift 2
      ;;
    --pattern)
      PATTERN=${2:?--pattern requires a glob}
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      printf 'unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$INPUT" ]]; then
        printf 'unexpected argument: %s\n' "$1" >&2
        usage >&2
        exit 2
      fi
      INPUT=$1
      shift
      ;;
  esac
done

if [[ -z "$INPUT" ]]; then
  usage >&2
  exit 2
fi
if [[ ! -e "$INPUT" ]]; then
  printf 'input does not exist: %s\n' "$INPUT" >&2
  exit 2
fi
INPUT=$(readlink -f "$INPUT")

if [[ -z "$OUTPUT_DIR" ]]; then
  mkdir -p "$ROOT/outputs" || exit 2
  OUTPUT_DIR=$(mktemp -d "$ROOT/outputs/run_XXXXXX") || exit 2
else
  mkdir -p "$OUTPUT_DIR" || exit 2
  OUTPUT_DIR=$(readlink -f "$OUTPUT_DIR")
fi
LOGS="$OUTPUT_DIR/logs"
mkdir -p "$LOGS" || exit 2
printf '日志=%s\n输入=%s\n' "$OUTPUT_DIR" "$INPUT"

cmd=(eeg-filter "$INPUT" --output-dir "$OUTPUT_DIR" --pattern "$PATTERN")
if [[ -n "$CONFIG" ]]; then
  cmd+=(--config "$CONFIG")
fi

"${cmd[@]}" --dry-run 2>&1 | tee "$LOGS/preflight.log"
preflight_rc=${PIPESTATUS[0]}
if [[ "$preflight_rc" -ne 0 ]]; then
  printf 'preflight_exit=%s\n' "$preflight_rc" > "$LOGS/status.txt"
  exit "$preflight_rc"
fi

"${cmd[@]}" 2>&1 | tee "$LOGS/filter.log"
filter_rc=${PIPESTATUS[0]}

if [[ -f "$INPUT" ]]; then
  denoised="$OUTPUT_DIR/observation.eeg_denoise.parquet"
  eeg-repair-qc "$denoised" > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
elif find "$INPUT" -name 'observation.eeg_denoise.parquet' -print -quit | grep -q .; then
  eeg-repair-qc "$INPUT" --pattern 'observation.eeg_denoise.parquet' \
    > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
else
  eeg-repair-qc "$OUTPUT_DIR" --pattern 'observation.eeg_denoise.parquet' \
    > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
fi
qc_rc=${PIPESTATUS[0]}
printf 'filter_exit=%s\nqc_exit=%s\n' "$filter_rc" "$qc_rc" > "$LOGS/status.txt"
if [[ "$filter_rc" -ne 0 ]]; then
  exit "$filter_rc"
fi
exit "$qc_rc"
