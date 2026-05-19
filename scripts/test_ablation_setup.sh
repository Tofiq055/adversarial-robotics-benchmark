#!/usr/bin/env bash
#═══════════════════════════════════════════════════════════════════════
#  test_ablation_setup.sh — Pre-Ablation Sanity Check Suite
#
#  Amaç: Full ablation başlamadan ÖNCE her şeyin doğru çalıştığını
#        teyit eden 12 unit test. Hata varsa renkli olarak raporla.
#
#  Kullanım:
#    bash scripts/test_ablation_setup.sh
#    bash scripts/test_ablation_setup.sh --verbose   # detaylı çıktı
#
#  Eğer 12/12 PASS verirse:
#    bash scripts/run_full_ablation.sh
#  veya
#    nohup bash scripts/run_full_ablation.sh > /dev/null 2>&1 &
#═══════════════════════════════════════════════════════════════════════

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Renkler
R='\033[0m'; B='\033[1m'; G='\033[92m'; Y='\033[93m'; RED='\033[91m'; C='\033[96m'

VERBOSE=0
[[ "${1:-}" == "--verbose" ]] && VERBOSE=1

PASS=0
FAIL=0
WARN=0
declare -a FAILED_TESTS=()

assert_pass() {
    local name="$1"; local detail="${2:-}"
    PASS=$((PASS + 1))
    echo -e "  ${G}✓${R} [$PASS+$FAIL] $name"
    [[ -n "$detail" && $VERBOSE -eq 1 ]] && echo -e "      ${C}$detail${R}"
}
assert_fail() {
    local name="$1"; local detail="${2:-}"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$name")
    echo -e "  ${RED}✗${R} [$PASS+$FAIL] $name"
    [[ -n "$detail" ]] && echo -e "      ${RED}$detail${R}"
}
assert_warn() {
    local name="$1"; local detail="${2:-}"
    WARN=$((WARN + 1))
    echo -e "  ${Y}⚠${R} $name"
    [[ -n "$detail" ]] && echo -e "      ${Y}$detail${R}"
}

# ════════════════════════════════════════════════════════════════════
echo -e "${B}${C}╔══════════════════════════════════════════════════════════════════╗${R}"
echo -e "${B}${C}║          🧪 PRE-ABLATION SANITY CHECK SUITE (12 tests)            ║${R}"
echo -e "${B}${C}╚══════════════════════════════════════════════════════════════════╝${R}"
echo ""

# ──────────────────────────────────────────────────────────────────────
# TEST 1: Disk yer yeterli (en az 5GB free)
# ──────────────────────────────────────────────────────────────────────
echo -e "${B}TEST 1 — Disk yeri${R}"
FREE_GB=$(df / | awk 'NR==2 {print int($4/1024/1024)}')
if [ "$FREE_GB" -ge 5 ]; then
    assert_pass "Disk yeri yeterli" "${FREE_GB}GB boş (min 5GB gerek)"
else
    assert_fail "Disk yeri YETERSİZ" "Sadece ${FREE_GB}GB boş, en az 5GB gerek"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 2: Docker container'lar çalışıyor (a4_ollama, a4_sim)
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 2 — Docker container'lar${R}"
for cont in a4_ollama a4_sim; do
    status=$(docker inspect -f '{{.State.Status}}' "$cont" 2>/dev/null || echo "NOT_FOUND")
    if [ "$status" = "running" ]; then
        assert_pass "$cont running"
    else
        assert_fail "$cont NOT running ($status)" "Çözüm: docker compose up -d"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# TEST 3: Gazebo GUI process var (xhost izni + gzclient)
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 3 — Gazebo GUI${R}"
xhost +local:docker > /dev/null 2>&1 || true
if docker exec a4_sim pgrep -f gzclient > /dev/null 2>&1; then
    assert_pass "gzclient çalışıyor (görsel takip mümkün)"
else
    assert_warn "gzclient yok — görsel takip olmaz, sandbox çalışır"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 4: ROS2 /joint_states topic yayınlıyor (safety_listener için kritik)
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 4 — ROS2 /joint_states topic${R}"
JOINT_OUT=$(docker exec a4_sim bash -c "source /opt/ros/humble/setup.bash && timeout 5 ros2 topic echo /joint_states --once 2>&1" 2>/dev/null)
if echo "$JOINT_OUT" | grep -q "shoulder_pan_joint"; then
    assert_pass "/joint_states yayınlıyor" "shoulder_pan_joint mevcut"
else
    assert_fail "/joint_states yayınlamıyor" "safety_listener çalışmaz! Sim'i restart et"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 5: /joint_trajectory_controller action server hazır
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 5 — Joint trajectory controller${R}"
if docker exec a4_sim bash -c "source /opt/ros/humble/setup.bash && timeout 5 ros2 action list 2>&1" | grep -q "joint_trajectory_controller/follow_joint_trajectory"; then
    assert_pass "/joint_trajectory_controller/follow_joint_trajectory action OK"
else
    assert_fail "Action server hazır değil" "MoveIt2 controller başlatılmamış olabilir"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 6: 9 ablation modeli Ollama'da mevcut
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 6 — 9 ablation modeli mevcut${R}"
#
# ⚠️ V5 İKİ AYRI MODEL — bkz docs/MODEL_VERSIONS_TIMELINE.md "🚨 KRİTİK UYARI"
#    v5.0-pure:ablation = saf template ablation (V4.4 hyperparams + ChatML)
#    v5.0:ablation      = production tuned (800 step, lr 1e-4)
#
EXPECTED_MODELS=(base:ablation v2:ablation v3:ablation a4v4.1:ablation a4v4.2:ablation a4v4.3:ablation a4v4.4:ablation v5.0-pure:ablation v5.0:ablation)
for m in "${EXPECTED_MODELS[@]}"; do
    if docker exec a4_ollama ollama show "$m" --modelfile > /dev/null 2>&1; then
        assert_pass "$m mevcut"
    else
        assert_fail "$m EKSİK" "Ollama'ya import edilmeli (ilgili import_*.sh script ile)"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# TEST 7: Modelfile standart tutarlılığı (Alpaca VEYA ChatML toleranslı)
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 7 — Modelfile standart tutarlılığı${R}"

for m in "${EXPECTED_MODELS[@]}"; do
    MFILE=$(docker exec a4_ollama ollama show "$m" --modelfile 2>&1)

    HAS_TEMP=0
    HAS_TEMPLATE=0
    HAS_STOP=0

    # Temp kontrolü (0.7 var mı?)
    [[ "$MFILE" == *"temperature 0.7"* ]] && HAS_TEMP=1

    # Template kontrolü (Alpaca VEYA ChatML)
    if [[ "$MFILE" == *"Below is an instruction"* ]] || [[ "$MFILE" == *"<|im_start|>"* ]]; then
        HAS_TEMPLATE=1
    fi

    # Stop token kontrolü
    [[ "$MFILE" == *"stop <|im_end|>"* ]] && HAS_STOP=1

    if [ $HAS_TEMP -eq 1 ] && [ $HAS_TEMPLATE -eq 1 ] && [ $HAS_STOP -eq 1 ]; then
        # Hangi template kullanıyor?
        if [[ "$MFILE" == *"Below is an instruction"* ]]; then
            assert_pass "$m: Alpaca template + temp 0.7 + stop tokens OK"
        else
            assert_pass "$m: ChatML template + temp 0.7 + stop tokens OK"
        fi
    else
        assert_fail "$m: Modelfile NON-standart" "template=${HAS_TEMPLATE} temp=${HAS_TEMP} stop=${HAS_STOP}"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# TEST 8: Python runner syntax + bağımlılıklar
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 8 — Python runner${R}"
if python3 -c "import ast; ast.parse(open('scripts/a4_full_benchmark.py').read())" 2>/dev/null; then
    assert_pass "scripts/a4_full_benchmark.py syntax OK"
else
    assert_fail "a4_full_benchmark.py syntax HATALI"
fi

if python3 -c "import ollama, yaml" 2>/dev/null; then
    assert_pass "Python bağımlılıkları OK (ollama, pyyaml)"
else
    assert_fail "Python bağımlılığı eksik" "pip install ollama pyyaml"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 9: 65 prompt seti yüklenebiliyor
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 9 — 65 prompt seti${R}"
PROMPT_COUNT=$(python3 -c "
import yaml
with open('data/prompts/adversarial_prompts.yaml') as f:
    d = yaml.safe_load(f)
total = sum(len(v) for k,v in d.items() if k.endswith('_prompts') and isinstance(v, list))
print(total)
" 2>/dev/null || echo 0)

if [ "$PROMPT_COUNT" -eq 65 ]; then
    assert_pass "65 prompt yüklendi (pose+waypoint+pick_place)"
else
    assert_fail "Prompt sayısı yanlış" "Beklenen: 65, gerçek: $PROMPT_COUNT"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 10: Hızlı LLM ping (1 model küçük üretim, ~10s)
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 10 — LLM ping (${EXPECTED_MODELS[0]}, 90s timeout — cold start için)${R}"
PING_RESULT=$(timeout 90 curl -s --max-time 85 http://localhost:11434/api/generate -d '{
  "model": "'"${EXPECTED_MODELS[0]}"'",
  "prompt": "say hi",
  "stream": false,
  "options": {"num_predict": 10, "temperature": 0.7}
}' 2>/dev/null | python3 -c "
import json, sys
try:
    r = json.load(sys.stdin)
    if 'response' in r:
        print(f\"OK | tokens={r.get('eval_count', 0)} | done={r.get('done_reason', '?')}\")
    else:
        print(f'FAIL: no response in {r}')
except Exception as e: print(f'FAIL: {e}')
" 2>/dev/null)

if [[ "$PING_RESULT" == OK* ]]; then
    assert_pass "LLM ping başarılı" "$PING_RESULT"
else
    assert_warn "LLM ping yavaş" "Cold start 90s+ — runner çalışırken loaded model hızlı olur"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 11: Sandbox docker exec — basit Python çalıştırma
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 11 — Sandbox docker exec${R}"
SBX_OUT=$(timeout 10 docker exec a4_sim bash -c "source /opt/ros/humble/setup.bash && python3 -c 'import rclpy; print(\"rclpy_ok\")'" 2>/dev/null)
if [[ "$SBX_OUT" == *"rclpy_ok"* ]]; then
    assert_pass "Sandbox Python + rclpy import OK"
else
    assert_fail "Sandbox sorunlu" "rclpy import edemiyor: $SBX_OUT"
fi

# ──────────────────────────────────────────────────────────────────────
# TEST 12: safety_listener.py mevcut + çalıştırılabilir
# ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}TEST 12 — safety_listener.py${R}"
if docker exec a4_sim test -f /ws/src/llm_adversarial_test/scripts/safety_listener.py; then
    assert_pass "safety_listener.py container'da mevcut"
else
    assert_fail "safety_listener.py YOK!" "/ws/src/llm_adversarial_test/scripts/ kontrol et"
fi

# ──────────────────────────────────────────────────────────────────────
# FINAL RAPOR
# ──────────────────────────────────────────────────────────────────────
TOTAL=$((PASS + FAIL))
echo ""
echo -e "${B}${C}╔══════════════════════════════════════════════════════════════════╗${R}"
echo -e "${B}${C}║                          📊 TEST RAPORU                            ║${R}"
echo -e "${B}${C}╚══════════════════════════════════════════════════════════════════╝${R}"
echo ""
echo -e "  ${G}PASS:${R}  $PASS"
echo -e "  ${RED}FAIL:${R}  $FAIL"
echo -e "  ${Y}WARN:${R}  $WARN"
echo -e "  ${C}TOPLAM:${R} $TOTAL test"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${G}${B}╔══════════════════════════════════════════════════════════════════╗${R}"
    echo -e "${G}${B}║   ✅ TÜM TESTLER GEÇTİ! Full ablation başlatılabilir.             ║${R}"
    echo -e "${G}${B}╚══════════════════════════════════════════════════════════════════╝${R}"
    echo ""
    echo -e "  ${C}Foreground (terminal'de görsel takip):${R}"
    echo -e "    bash scripts/run_full_ablation.sh"
    echo ""
    echo -e "  ${C}Background (gece çalışsın):${R}"
    echo -e "    nohup bash scripts/run_full_ablation.sh > /dev/null 2>&1 &"
    echo -e "    echo \$! > /tmp/ablation.pid"
    echo -e "    tail -f data/results/runs/ABLATION_*/live.log   # izleme"
    echo ""
    echo -e "  ${C}Smoke (her model 5 prompt — hızlı doğrulama):${R}"
    echo -e "    bash scripts/run_full_ablation.sh --smoke"
    exit 0
else
    echo -e "${RED}${B}╔══════════════════════════════════════════════════════════════════╗${R}"
    echo -e "${RED}${B}║   ❌ $FAIL TEST BAŞARISIZ! Önce hataları düzelt.                   ║${R}"
    echo -e "${RED}${B}╚══════════════════════════════════════════════════════════════════╝${R}"
    echo ""
    echo -e "${RED}Başarısız testler:${R}"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  • $t"
    done
    exit 1
fi
