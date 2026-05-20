# Qwen LoRA Fine-Tune Templates

Two ready-to-run, modular Kaggle notebooks for fine-tuning **Qwen3.5-4B** (or any HuggingFace causal-LM) with LoRA on dual T4 GPUs, exporting to **Q6_K GGUF**, and pushing to **HuggingFace Hub**.

**License:** MIT

---

## 📒 Notebooks

| Notebook | Training Format |
|---|---|
| [`qwen-lora-finetune-chatml.ipynb`](qwen-lora-finetune-chatml.ipynb) | ChatML — `<\|im_start\|>system ... <\|im_end\|>` |
| [`qwen-lora-finetune-alpaca.ipynb`](qwen-lora-finetune-alpaca.ipynb) | Alpaca — `### System: / ### Instruction: / ### Response:` |

The notebooks are **identical** except for the dataset formatting in cell `[6/12]`. Pick the one that matches your inference target template.

---

## 🚀 Quick Start

1. Upload one of the notebooks to **Kaggle**, choose **Dual T4 GPU**.
2. Add a Kaggle Secret named **`HF_TOKEN`** (Add-ons → Secrets) — required for HuggingFace push.
3. Open the **`[CONFIG]`** cell (right after the title) and edit:
   ```python
   BASE_MODEL    = "Qwen/Qwen3.5-4B"
   DATASET_PATH  = "/kaggle/input/your-dataset/your_data.jsonl"
   HF_REPO       = "your-username/your-model-name"
   SYSTEM_PROMPT = "<your task instructions>"
   MAX_STEPS     = 1000
   LR            = 5e-5
   LORA_R        = 16
   ```
4. **Run All**. Total time on dual T4: ~3 hours for 1000 steps + ~15 min for GGUF export + push.

---

## 📦 Dataset Format

JSONL with one example per line:

```json
{"instruction": "Write a Python function that computes Fibonacci numbers.", "response": "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a"}
{"instruction": "...", "response": "..."}
```

Two required string fields: `instruction` and `response`. Any other fields are ignored.

---

## 📤 Output Artifacts

After a successful run, your HuggingFace repo will contain:

- `model_Q6_K.gguf` — quantized model (~3.3 GB), ready for `llama.cpp` / Ollama
- LoRA adapter is kept in `OUTPUT_DIR` (default `./lora_adapter`) on the Kaggle session

To use the GGUF in **Ollama**:

```bash
# Download the GGUF locally
huggingface-cli download your-username/your-model-name model_Q6_K.gguf --local-dir ./

# Create an Ollama Modelfile
cat > Modelfile <<EOF
FROM ./model_Q6_K.gguf
PARAMETER temperature 0.7
PARAMETER stop "<|im_end|>"   # ChatML — drop these for Alpaca
PARAMETER stop "<|im_start|>"
SYSTEM "<paste your SYSTEM_PROMPT here>"
EOF

ollama create my-model -f Modelfile
ollama run my-model
```

---

## ⚙️ Hyperparameters Cheat Sheet

| Parameter | Default | Notes |
|---|---:|---|
| `MAX_STEPS` | 1000 | More steps = longer training; loss usually plateaus by 800-1200 |
| `LR` | 5e-5 | Lower (5e-5) is stable; higher (1e-4) converges faster but can overfit |
| `LORA_R` | 16 | 8 = lighter, 16 = standard, 32 = more capacity |
| `LORA_ALPHA` | 32 | Common: `alpha = 2 * r` |
| `BATCH` × `GRAD_ACCUM` | 1 × 4 | Effective batch on dual T4 = `1 * 2 * 4 = 8` |
| `MAX_SEQ_LEN` | 2048 | Lower if you OOM, higher if your dataset has long examples |
| `WARMUP_STEPS` | 50 | LR ramps up over the first N steps |
| `SEED` | 3407 | Reproducibility |

---

## 🖥️ Hardware Notes

- **Tested on:** Kaggle Dual T4 GPU (2 × 15 GB VRAM)
- **VRAM usage:** ~13 GB per GPU during training (FP16 + LoRA + gradient checkpointing)
- **Disk:** ~15 GB free needed in `/kaggle/working` (for merged model + GGUF)
- **A100/H100:** Will work without changes; ~3-5× faster

---

## 🛠 Troubleshooting

| Symptom | Fix |
|---|---|
| `OOM` during training | Lower `MAX_SEQ_LEN` to 1024, or `LORA_R` to 8 |
| `OOM` during merge | The notebook uses CPU merge — make sure you didn't override |
| `HF_TOKEN not found` | Add the Kaggle Secret in **Add-ons → Secrets**, name = `HF_TOKEN` |
| GGUF conversion fails | Notebook auto-downloads original tokenizer to fix the llama.cpp hash mismatch — re-run cell `[11/12]` |
| Empty / nonsense outputs | Make sure your inference template **matches** the training template (ChatML notebook → ChatML inference) |
