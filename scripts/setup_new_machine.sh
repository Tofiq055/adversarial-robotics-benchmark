#!/bin/bash
###############################################################################
# A4 Adversarial Robotics — Yeni Makine Kurulum Scripti
# =====================================================
# Bu script, ablation testlerini sıfırdan çalıştırmak için gereken
# TÜM bağımlılıkları tek seferde kurar.
#
# Kullanım:
#   chmod +x scripts/setup_new_machine.sh
#   ./scripts/setup_new_machine.sh
#
# Gereksinimler:
#   - Ubuntu 22.04+ (veya uyumlu Linux)
#   - NVIDIA GPU + CUDA sürücüleri kurulu
#   - İnternet bağlantısı
#   - git kurulu
###############################################################################

set -e  # Hata olursa dur

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

STEP=0
total_steps=8

step() {
    STEP=$((STEP + 1))
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}  [${STEP}/${total_steps}] $1${NC}"
    echo -e "${BLUE}============================================================${NC}"
}

success() {
    echo -e "  ${GREEN}✅ $1${NC}"
}

warn() {
    echo -e "  ${YELLOW}⚠️  $1${NC}"
}

fail() {
    echo -e "  ${RED}❌ $1${NC}"
    exit 1
}

# Projenin kök dizinine git
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
echo -e "${GREEN}Proje dizini: ${PROJECT_DIR}${NC}"

###############################################################################
# 1. NVIDIA GPU Kontrolü
###############################################################################
step "NVIDIA GPU Kontrolü"

if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    GPU_DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
    success "GPU bulundu: ${GPU_NAME} (${GPU_VRAM})"
    success "Sürücü versiyonu: ${GPU_DRIVER}"
else
    fail "nvidia-smi bulunamadı! NVIDIA sürücülerini kurun: sudo apt install nvidia-driver-535"
fi

###############################################################################
# 2. Python 3 ve pip Kontrolü
###############################################################################
step "Python 3 ve pip Kontrolü"

if command -v python3 &> /dev/null; then
    PYTHON_VER=$(python3 --version)
    success "Python bulundu: ${PYTHON_VER}"
else
    warn "Python3 bulunamadı, kuruluyor..."
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv
fi

###############################################################################
# 3. Python Bağımlılıklarını Kur
###############################################################################
step "Python Bağımlılıkları Kuruluyor"

pip3 install --user ollama pyyaml python-dotenv huggingface_hub 2>/dev/null
success "ollama (Python client)"
success "pyyaml"
success "python-dotenv"
success "huggingface_hub"

# Doğrulama
python3 -c "import ollama; import yaml; print('✅ Tüm Python modülleri yüklü')"

###############################################################################
# 4. Ollama Kurulumu
###############################################################################
step "Ollama Kurulumu"

if command -v ollama &> /dev/null; then
    OLLAMA_VER=$(ollama --version)
    success "Ollama zaten kurulu: ${OLLAMA_VER}"
else
    warn "Ollama kuruluyor..."
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama kuruldu"
fi

# Ollama servisinin çalıştığını kontrol et
if ollama list &> /dev/null; then
    success "Ollama servisi çalışıyor"
else
    warn "Ollama servisi başlatılıyor..."
    # systemd varsa
    if systemctl is-active --quiet ollama 2>/dev/null; then
        success "Ollama systemd ile çalışıyor"
    else
        nohup ollama serve > /dev/null 2>&1 &
        sleep 3
        success "Ollama arka planda başlatıldı"
    fi
fi

###############################################################################
# 5. Base Model İndir (qwen3:4b)
###############################################################################
step "Base Model İndiriliyor (qwen3:4b) — ~2.5 GB"

if ollama list 2>/dev/null | grep -q "qwen3:4b"; then
    success "qwen3:4b zaten mevcut"
else
    echo "  İndirme başlıyor... (Birkaç dakika sürebilir)"
    ollama pull qwen3:4b
    success "qwen3:4b indirildi"
fi

###############################################################################
# 6. V3 Golden Model — HuggingFace'den GGUF İndir ve Ollama'ya Yükle
###############################################################################
step "V3 Golden Model (Fine-tuned) Kuruluyor — ~4.5 GB"

GGUF_DIR="${PROJECT_DIR}/data/models/v3_golden"
GGUF_FILE="${GGUF_DIR}/a4-v3-golden-q8.gguf"
MODELFILE="${GGUF_DIR}/Modelfile"

mkdir -p "$GGUF_DIR"

if ollama list 2>/dev/null | grep -q "a4v3golden"; then
    success "a4v3golden zaten Ollama'da mevcut"
else
    # GGUF dosyasını HuggingFace'den indir
    if [ ! -f "$GGUF_FILE" ]; then
        echo "  GGUF dosyası HuggingFace'den indiriliyor..."
        echo "  ⚠️  Bu PRIVATE bir repo. HF_TOKEN gerekli!"
        
        # .env dosyasından token'ı oku
        if [ -f "${PROJECT_DIR}/.env" ]; then
            export $(grep HF_TOKEN "${PROJECT_DIR}/.env" | xargs)
        fi
        
        if [ -z "$HF_TOKEN" ]; then
            fail "HF_TOKEN bulunamadı! .env dosyasına HF_TOKEN=hf_xxx ekleyin."
        fi
        
        python3 -c "
from huggingface_hub import hf_hub_download
import os

token = os.environ.get('HF_TOKEN')
print('  HuggingFace\'den indirme başlatılıyor...')
path = hf_hub_download(
    repo_id='<your-username>/<your-model>'  # set your own HF repo,
    filename='a4-v3-golden-q8.gguf',
    local_dir='${GGUF_DIR}',
    token=token
)
print(f'  İndirildi: {path}')
"
        success "GGUF dosyası indirildi"
    else
        success "GGUF dosyası zaten mevcut: ${GGUF_FILE}"
    fi
    
    # Modelfile oluştur
    echo "FROM ${GGUF_FILE}" > "$MODELFILE"
    
    # Ollama'ya yükle
    echo "  Ollama'ya model yükleniyor..."
    ollama create a4v3golden -f "$MODELFILE"
    success "a4v3golden Ollama'ya yüklendi"
fi

###############################################################################
# 7. .env Dosyası Kontrolü
###############################################################################
step ".env Dosyası Kontrolü"

ENV_FILE="${PROJECT_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    success ".env dosyası mevcut"
    # İçerik kontrolü
    grep -q "OLLAMA_HOST" "$ENV_FILE" && success "OLLAMA_HOST tanımlı" || warn "OLLAMA_HOST eksik"
    grep -q "HF_TOKEN" "$ENV_FILE" && success "HF_TOKEN tanımlı" || warn "HF_TOKEN eksik"
else
    warn ".env dosyası bulunamadı! Örnek oluşturuluyor..."
    cat > "$ENV_FILE" << 'ENVEOF'
# A4 Project Environment Variables
OLLAMA_HOST=http://127.0.0.1:11434
HF_TOKEN=hf_BURAYA_KENDI_TOKENINIZI_YAZIN
ENVEOF
    warn "Lütfen .env dosyasını düzenleyin ve HF_TOKEN'ınızı girin!"
fi

###############################################################################
# 8. Son Doğrulama
###############################################################################
step "Son Doğrulama"

echo ""
echo "  📋 Sistem Bilgileri:"
echo "  ─────────────────────────────────────────"
echo "  GPU         : $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
echo "  VRAM        : $(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)"
echo "  GPU Sıcaklık: $(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null | head -1)°C"
echo "  Python      : $(python3 --version 2>&1)"
echo "  Ollama      : $(ollama --version 2>&1)"
echo "  Disk Boş    : $(df -h . | tail -1 | awk '{print $4}')"
echo ""
echo "  📦 Ollama Modelleri:"
ollama list 2>/dev/null | while read -r line; do
    echo "    $line"
done
echo ""

# Hızlı test — modelin GPU'da çalıştığını doğrula
echo "  🧪 Hızlı GPU Inference Testi (qwen3:4b)..."
START_TIME=$(date +%s%N)
RESPONSE=$(python3 -c "
import ollama
r = ollama.chat(model='qwen3:4b', messages=[{'role':'user','content':'Say OK'}], options={'num_predict': 10})
print(r['message']['content'][:50])
" 2>&1)
END_TIME=$(date +%s%N)
ELAPSED=$(( (END_TIME - START_TIME) / 1000000 ))

echo "  Yanıt: ${RESPONSE}"
echo "  Süre: ${ELAPSED}ms"

# GPU kullanım kontrolü
GPU_PROC=$(ollama ps 2>/dev/null | grep "GPU" | head -1)
if echo "$GPU_PROC" | grep -q "GPU"; then
    success "Model GPU üzerinde çalışıyor ✅"
else
    warn "Model CPU üzerinde çalışıyor olabilir! nvidia-smi kontrol edin."
fi

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  🎉 KURULUM TAMAMLANDI!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Testleri başlatmak için:"
echo ""
echo "  1️⃣  Tüm Ablasyon Sürecini Başlat:"
echo "      make ablation"
echo ""
echo "  2️⃣  Hızlı Duman Testi (Smoke Test):"
echo "      make smoke"
echo ""
echo "  📊 Sonuçlar:"
echo "      data/results/AGGREGATE_ABLATION.md"
echo "      data/results/runs/"
echo ""
