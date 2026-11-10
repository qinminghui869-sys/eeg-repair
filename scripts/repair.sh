#!/usr/bin/env bash
# Preflight, repair, then structural QC.
# Dataset mode writes observation.eeg_repair.parquet beside the source.
# The original observation.eeg.parquet is never overwritten.
set -uo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/repair.sh INPUT [options]

Run preflight, repair, and structural QC for one EEG Parquet file,
one dataset directory, or a batch directory.

Options:
  --config FILE       Optional algorithm settings or missing metadata
  --output-dir DIR    Logs, and results for loose Parquet files
                      Default: <repo>/outputs/run_XXXXXX
  --pattern GLOB      Input filename when INPUT is a directory
                      Default: observation.eeg.parquet
  --impedance FILE    Single-file mode only
  -h, --help          Show this help

Exit status is preflight, otherwise repair, otherwise QC.
Logs: <output-dir>/logs/preflight.log, repair.log, output_qc.json, status.txt

Dataset mode writes observation.eeg_repair.parquet back into the source
dataset. --output-dir then receives logs only.
EOF
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT" || exit 2
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate || exit 2
fi
export MNE_DONTWRITE_HOME=true

if ! command -v eeg-repair >/dev/null 2>&1 || ! command -v eeg-repair-qc >/dev/null 2>&1; then
  printf 'eeg-repair is not on PATH. From %s run: uv sync --locked --extra dev --python 3.11 && source .venv/bin/activate\n' "$ROOT" >&2
  exit 2
fi

INPUT=""
CONFIG=""
OUTPUT_DIR=""
PATTERN="observation.eeg.parquet"
IMPEDANCE=""

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
    --impedance)
      IMPEDANCE=${2:?--impedance requires a file}
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

cmd=(eeg-repair "$INPUT" --output-dir "$OUTPUT_DIR" --pattern "$PATTERN")
if [[ -n "$CONFIG" ]]; then
  cmd+=(--config "$CONFIG")
fi
if [[ -n "$IMPEDANCE" ]]; then
  cmd+=(--impedance "$IMPEDANCE")
fi

"${cmd[@]}" --dry-run 2>&1 | tee "$LOGS/preflight.log"
preflight_rc=${PIPESTATUS[0]}
if [[ "$preflight_rc" -ne 0 ]]; then
  printf 'preflight_exit=%s\n' "$preflight_rc" > "$LOGS/status.txt"
  exit "$preflight_rc"
fi

"${cmd[@]}" 2>&1 | tee "$LOGS/repair.log"
repair_rc=${PIPESTATUS[0]}

if [[ -f "$INPUT" ]]; then
  repaired="$OUTPUT_DIR/$(basename "${INPUT%.parquet}")_repair.parquet"
  eeg-repair-qc "$repaired" > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
elif find "$INPUT" -name 'observation.eeg_repair.parquet' -print -quit | grep -q .; then
  eeg-repair-qc "$INPUT" --pattern 'observation.eeg_repair.parquet' \
    > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
else
  eeg-repair-qc "$OUTPUT_DIR" --pattern '*_repair.parquet' \
    > "$LOGS/output_qc.json" 2> "$LOGS/output_qc.stderr.log"
fi
qc_rc=${PIPESTATUS[0]}
printf 'repair_exit=%s\nqc_exit=%s\n' "$repair_rc" "$qc_rc" > "$LOGS/status.txt"
if [[ "$repair_rc" -ne 0 ]]; then
  exit "$repair_rc"
fi
exit "$qc_rc"
