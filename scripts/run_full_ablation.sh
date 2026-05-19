#!/usr/bin/env bash
#═══════════════════════════════════════════════════════════════════════
# run_full_ablation.sh — Full Academic Ablation Orchestrator
#
# WHAT IT DOES (single foreground stream — see everything live):
# 1. Bring docker compose up if needed.
# 2. Run pre-flight via a4_full_benchmark.py (--skip-preflight skipped).
# 3. Launch the benchmark IN-PROCESS for ALL models, ALL prompts.
# The benchmark itself handles per-model transition cleanup
# (kill zombies, sim reset, ollama unload, joint_states settle).
# 4. Each prompt produces (live, before moving on):
# • LLM stream (color-coded think/code)
# • Sandbox run with /reset_simulation between prompts
# • Static analysis (pattern-based)
# • Dynamic CSV (joint_states ~100 Hz time series)
# • Per-prompt fsync to results.jsonl
# 5. Output dir layout:
# data/results/runs/RUN_<ts>_<hash>/
# ├── ENVIRONMENT.md
# ├── ACADEMIC_HYPOTHESES.md
# ├── MODEL_METADATA.md
# ├── run_config.json
# ├── live.log
# ├── results.jsonl
# ├── model_transitions.log
# ├── generated_scripts/<model>/<prompt>.py
# ├── static_analysis/<model>/<prompt>.json
# ├── dynamic_analysis/<model>/<prompt>.csv + .summary.json
# └── summary.md (auto on completion)
#
# USAGE:
# bash scripts/run_full_ablation.sh # full 65×9
# bash scripts/run_full_ablation.sh --smoke # first 5 prompts
# bash scripts/run_full_ablation.sh --resume RUN_xxx # continue a run
#
# IMPORTANT — run in a real terminal (not nohup). The benchmark
# streams LLM tokens with ANSI colors; you want to SEE it live.
#═══════════════════════════════════════════════════════════════════════
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
R='\033[0m'; B='\033[1m'; G='\033[92m'; Y='\033[93m'; RED='\033[91m'; C='\033[96m'; M='\033[95m'

# ── Models — EDIT HERE if adding a new model ─────────────────────────
#
# [WARN] V5 IS TWO SEPARATE MODELS — see docs/MODEL_VERSIONS_TIMELINE.md
# v5.0:ablation = production tuned (800 steps, lr=1e-4)
# v5.0-pure:ablation = pure template ablation (1000 steps, lr=5e-5,
# V4.4 hyperparams, only template differs)
#
MODELS=(
 "base:ablation"
 "v2:ablation"
 "v3:ablation"
 "a4v4.1:ablation"
 "a4v4.2:ablation"
 "a4v4.3:ablation"
 "a4v4.4:ablation"
 "v5.0:ablation"
 "v5.0-pure:ablation"
)

# ── Inference parameters (must be IDENTICAL across all models) ──────
TEMP=0.7
SEED=3407
PENALTY=1.2
PREDICT=4096
SANDBOX_TIMEOUT=30
LLM_TIMEOUT=180
MAX_CHARS=10000

# ── Argument parsing ────────────────────────────────────────────────
EXTRA_ARGS=()
RUN_LABEL="full"
while [[ $# -gt 0 ]]; do
 case "$1" in
 --smoke)
 EXTRA_ARGS+=("--limit" "5")
 RUN_LABEL="smoke5"
 echo " SMOKE mode — first 5 prompts per model"
 shift ;;
 --limit)
 EXTRA_ARGS+=("--limit" "$2")
 RUN_LABEL="limit$2"
 shift 2 ;;
 --resume)
 EXTRA_ARGS+=("--resume" "$2")
 shift 2 ;;
 --no-static)
 EXTRA_ARGS+=("--no-static")
 shift ;;
 --no-dynamic)
 EXTRA_ARGS+=("--no-dynamic")
 shift ;;
 --no-metadata)
 EXTRA_ARGS+=("--no-metadata")
 shift ;;
 --models)
 shift
 MODELS=()
 while [[ $# -gt 0 && "$1" != --* ]]; do MODELS+=("$1"); shift; done ;;
 -h|--help)
 head -50 "$0" | grep -E '^#' | sed 's/^# *//'
 exit 0 ;;
 *)
 echo "Unknown arg: $1"; exit 1 ;;
 esac
done

# ── Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${B}${M}╔════════════════════════════════════════════════════════════════════╗${R}"
echo -e "${B}${M}║ A4 FULL ACADEMIC ABLATION — SINGLE-RUN ORCHESTRATOR ║${R}"
echo -e "${B}${M}╚════════════════════════════════════════════════════════════════════╝${R}"
echo ""
echo -e " ${B}Models:${R} ${#MODELS[@]} (${MODELS[*]})"
echo -e " ${B}Mode:${R} $RUN_LABEL"
echo -e " ${B}Params:${R} temp=$TEMP seed=$SEED penalty=$PENALTY predict=$PREDICT"
echo -e " ${B}Timeouts:${R} sandbox=${SANDBOX_TIMEOUT}s llm=${LLM_TIMEOUT}s"
echo ""

# ── Bring containers up ──────────────────────────────────────────────
echo -e "${C}▶ Ensuring docker compose is up...${R}"
docker compose up -d 2>&1 | tail -3
sleep 4

# ── Quick container health (the benchmark does its own preflight too) ──
echo ""
echo -e "${C}▶ Container health:${R}"
for cont in a4_ollama a4_sim; do
 status=$(docker inspect -f '{{.State.Status}}' "$cont" 2>/dev/null || echo "MISSING")
 if [ "$status" = "running" ]; then
 echo -e " ${G}[OK]${R} $cont running"
 else
 echo -e " ${RED}[FAIL]${R} $cont NOT running ($status) — aborting"
 exit 2
 fi
done

# ── Optional Gazebo GUI hint ─────────────────────────────────────────
xhost +local:docker > /dev/null 2>&1 || true
if ! docker exec a4_sim pgrep -f gzclient > /dev/null 2>&1; then
 echo ""
 echo -e " ${Y}[WARN] gzclient not running — sandbox will execute but you won't see the arm.${R}"
 echo -e " To start GUI in another terminal:"
 echo -e " ${C}docker exec -d a4_sim bash -c 'source /opt/ros/humble/setup.bash && DISPLAY=:0 gzclient &'${R}"
fi

# ── Model availability check ─────────────────────────────────────────
echo ""
echo -e "${C}▶ Model availability:${R}"
MISSING=()
for m in "${MODELS[@]}"; do
 if docker exec a4_ollama ollama show "$m" --modelfile > /dev/null 2>&1; then
 echo -e " ${G}[OK]${R} $m"
 else
 echo -e " ${RED}[FAIL]${R} $m MISSING"
 MISSING+=("$m")
 fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
 echo ""
 echo -e "${RED}[FAIL] Missing models: ${MISSING[*]}${R}"
 echo -e "${Y} Import them first (see scripts/import_v50*.sh, etc).${R}"
 exit 3
fi

# ── Disk space sanity (warn if < 5 GB) ───────────────────────────────
FREE_GB=$(df / | awk 'NR==2 {print int($4/1024/1024)}')
echo ""
echo -e "${C}▶ Disk free: ${FREE_GB} GB${R}"
if [ "$FREE_GB" -lt 5 ]; then
 echo -e "${RED}[FAIL] Low disk (<5 GB). Aborting to prevent mid-run failure.${R}"
 exit 4
fi

# ── 5-second countdown so the user can Ctrl+C ─────────────────────────
echo ""
echo -e "${G}${B}▶ Starting in 5 seconds — Ctrl+C to abort.${R}"
for i in 5 4 3 2 1; do printf " ${i}..." ; sleep 1 ; done
echo -e " ${G}${B}GO${R}"

# ── Run benchmark IN-PROCESS (foreground, full color stream) ────────
ABLATION_START=$(date +%s)
python3 -u "$SCRIPT_DIR/a4_full_benchmark.py" \
 --models "${MODELS[@]}" \
 --temp "$TEMP" --seed "$SEED" --penalty "$PENALTY" \
 --predict "$PREDICT" --timeout "$SANDBOX_TIMEOUT" \
 --llm-timeout "$LLM_TIMEOUT" --max-chars "$MAX_CHARS" \
 "${EXTRA_ARGS[@]}"
EXIT=$?
ABLATION_END=$(date +%s)
ELAPSED=$((ABLATION_END - ABLATION_START))

echo ""
echo -e "${B}${M}╔════════════════════════════════════════════════════════════════════╗${R}"
if [ $EXIT -eq 0 ]; then
 echo -e "${B}${G}║ ABLATION COMPLETED ║${R}"
elif [ $EXIT -eq 130 ]; then
 echo -e "${B}${Y}║ [WARN] ABLATION INTERRUPTED — partial results saved ║${R}"
else
 echo -e "${B}${RED}║ [FAIL] ABLATION FAILED (exit=$EXIT) ║${R}"
fi
echo -e "${B}${M}╚════════════════════════════════════════════════════════════════════╝${R}"
echo ""
echo -e " Elapsed: $((ELAPSED / 3600))h $(((ELAPSED % 3600) / 60))m $((ELAPSED % 60))s"
echo ""
echo -e " Latest run: $(ls -t data/results/runs | head -1)"
echo -e " Aggregate analysis (after completion):"
echo -e " python3 scripts/comparative_analysis.py data/results/runs/<RUN_ID>/"

exit $EXIT
