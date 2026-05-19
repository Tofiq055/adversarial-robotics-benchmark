#!/usr/bin/env python3
"""
a4_full_benchmark.py — Sunum Kalitesinde A4 Adversarial Robot Benchmark

Tek scriptle:
 • Pre-flight kontroller (Docker, Ollama, Gazebo GUI, modeller)
 • Tek veya çoklu model — kod değişmeden parametrelerle
 • Her run kendi klasöründe: data/results/runs/RUN_<ts>_<models>/
 ├── run_config.json (parametreler + ortam)
 ├── results.jsonl (per-prompt sonuç)
 ├── live.log (terminal aynası)
 ├── generated_scripts/ (üretilen .py'ler model bazlı)
 └── summary.md (run sonu otomatik üretilir)
 • Stream LLM cevabı renkli (think=cyan, code=green, error=red)
 • Per-prompt panel + canlı progress bar
 • Ctrl+C / SIGTERM → partial summary üretir, veri kaybı yok
 • Resume: aynı RUN klasörüne devam etmek için --resume <RUN_ID>

Kullanım:
 # Tek model smoke
 python3 scripts/a4_full_benchmark.py --models a4v4.1:std --limit 3

 # Tam ablation (4 model × 65 prompt)
 python3 scripts/a4_full_benchmark.py --models base:std v2:std v4:std a4v4.1:std

 # Belirli prompt subseti
 python3 scripts/a4_full_benchmark.py --models a4v4.1:std --prompts pose_baseline,pose_jailbreak

 # Önceki bir run'a devam
 python3 scripts/a4_full_benchmark.py --resume RUN_20260512_140000_4models
"""

import argparse
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
 import yaml
except ImportError:
 print("\033[91m[FAIL] pyyaml eksik: pip install pyyaml\033[0m"); sys.exit(1)

try:
 import ollama
except ImportError:
 print("\033[91m[FAIL] ollama eksik: pip install ollama\033[0m"); sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
# RENK & GÖRSEL UTILITY
# ═══════════════════════════════════════════════════════════════════════
class C:
 R = "\033[0m"
 BOLD = "\033[1m"; DIM = "\033[2m"
 BLACK = "\033[30m"; RED = "\033[91m"; GREEN = "\033[92m"
 YELLOW = "\033[93m"; BLUE = "\033[94m"; MAGENTA = "\033[95m"; CYAN = "\033[96m"
 BG_GREEN = "\033[42m"; BG_RED = "\033[41m"; BG_YELLOW = "\033[43m"

WIDTH = shutil.get_terminal_size((100, 30)).columns

def hr(char="─", color=C.DIM):
 print(f"{color}{char * WIDTH}{C.R}")

def banner(title, sub=""):
 print()
 print(f"{C.BOLD}{C.MAGENTA}╔{'═' * (WIDTH-2)}╗{C.R}")
 print(f"{C.BOLD}{C.MAGENTA}║{title.center(WIDTH-2)}║{C.R}")
 if sub:
 print(f"{C.MAGENTA}║{sub.center(WIDTH-2)}║{C.R}")
 print(f"{C.BOLD}{C.MAGENTA}╚{'═' * (WIDTH-2)}╝{C.R}")

def section(title, color=C.CYAN):
 print()
 print(f"{color}{C.BOLD}▶ {title}{C.R}")
 hr()

def status_check(label, ok, detail=""):
 icon = f"{C.GREEN}[OK]{C.R}" if ok else f"{C.RED}[FAIL]{C.R}"
 color = C.GREEN if ok else C.RED
 line = f" [{icon}] {color}{label}{C.R}"
 if detail:
 line += f" {C.DIM}{detail}{C.R}"
 print(line)
 return ok

def progress_bar(current, total, width=40):
 filled = int(width * current / total) if total else 0
 bar = "█" * filled + "░" * (width - filled)
 pct = 100 * current / total if total else 0
 return f"{C.GREEN}{bar}{C.R} {current}/{total} ({pct:.1f}%)"


# ═══════════════════════════════════════════════════════════════════════
# PROJECT PATHS
# ═══════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_PATH = PROJECT_ROOT / "data" / "prompts" / "adversarial_prompts.yaml"
RUNS_DIR = PROJECT_ROOT / "data" / "results" / "runs"
STATUS_HOST = PROJECT_ROOT / "data" / "results" / "current_run_status.txt"
LISTENER_PATH = "/ws/src/llm_adversarial_test/scripts/safety_listener.py"
SIM_CONTAINER = "a4_sim"
OLLAMA_CONTAINER = "a4_ollama"
OLLAMA_HOST = "http://127.0.0.1:11434"


# ═══════════════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════════════
def docker_container_status(name):
 try:
 r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name],
 capture_output=True, text=True, timeout=5)
 return r.stdout.strip() if r.returncode == 0 else None
 except Exception:
 return None

def gazebo_gui_running():
 try:
 r = subprocess.run(["docker", "exec", SIM_CONTAINER, "pgrep", "-f", "gzclient"],
 capture_output=True, text=True, timeout=5)
 return r.returncode == 0 and r.stdout.strip() != ""
 except Exception:
 return False

def joint_states_publishing():
 try:
 r = subprocess.run(
 ["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "source /opt/ros/humble/setup.bash && timeout 3 ros2 topic echo /joint_states --once 2>&1 | grep -c shoulder_pan_joint"],
 capture_output=True, text=True, timeout=8)
 return r.returncode == 0 and int(r.stdout.strip() or "0") > 0
 except Exception:
 return False

def ollama_models_available(models):
 try:
 client = ollama.Client(host=OLLAMA_HOST)
 avail = {m['model'] for m in client.list().get('models', [])}
 # Some Ollama versions strip ":latest" suffix; normalize
 avail_norm = {m.split(':')[0] if m.endswith(':latest') else m for m in avail}
 missing = []
 for m in models:
 if m not in avail and m not in avail_norm:
 missing.append(m)
 return (len(missing) == 0, missing, avail)
 except Exception as e:
 return (False, models, set())

def preflight(models):
 section("PRE-FLIGHT CHECKS", C.CYAN)
 all_ok = True

 s = docker_container_status(OLLAMA_CONTAINER)
 all_ok &= status_check(f"Ollama container ({OLLAMA_CONTAINER})", s == "running",
 f"status: {s or 'NOT FOUND'}")

 s = docker_container_status(SIM_CONTAINER)
 all_ok &= status_check(f"Sim container ({SIM_CONTAINER})", s == "running",
 f"status: {s or 'NOT FOUND'}")
 if s != "running":
 print(f" {C.YELLOW}↳ Çözüm: docker start {SIM_CONTAINER}{C.R}")

 if s == "running":
 gui_ok = gazebo_gui_running()
 status_check("Gazebo GUI (gzclient)", gui_ok,
 "görsel sunum için gerekli" if not gui_ok else "")
 if not gui_ok:
 print(f" {C.YELLOW}↳ Çözüm: xhost +local:docker && docker restart {SIM_CONTAINER}{C.R}")

 js_ok = joint_states_publishing()
 all_ok &= status_check("/joint_states topic yayınlıyor", js_ok)

 ok, missing, _ = ollama_models_available(models)
 all_ok &= status_check(f"Modeller mevcut ({len(models)})", ok,
 f"eksik: {missing}" if missing else f"OK: {', '.join(models)}")
 if missing:
 print(f" {C.YELLOW}↳ Eksik modelleri ollama create ile oluştur (Modelfile_std){C.R}")

 return all_ok


# ═══════════════════════════════════════════════════════════════════════
# RUN DIRECTORY
# ═══════════════════════════════════════════════════════════════════════
def make_run_id(models):
 ts = datetime.now().strftime("%Y%m%d_%H%M%S")
 if len(models) == 1:
 tag = models[0].replace(":", "_").replace("/", "_")
 else:
 tag = f"{len(models)}models"
 return f"RUN_{ts}_{tag}"

def setup_run_dir(run_id, resume=False):
 rd = RUNS_DIR / run_id
 if rd.exists() and not resume:
 # Don't clobber an existing run; abort
 print(f"{C.RED}[FAIL] Run klasörü zaten var: {rd}{C.R}")
 print(f" Devam için: --resume {run_id}")
 sys.exit(1)
 rd.mkdir(parents=True, exist_ok=True)
 (rd / "generated_scripts").mkdir(exist_ok=True)
 (rd / "static_analysis").mkdir(exist_ok=True)
 (rd / "dynamic_analysis").mkdir(exist_ok=True)
 return rd


# ═══════════════════════════════════════════════════════════════════════
# AGGRESSIVE MODEL-TRANSITION CLEANUP
# Kills zombies, unloads previous model, resets sim, flushes buffers.
# Logged to model_transitions.log for academic provenance.
# ═══════════════════════════════════════════════════════════════════════
def model_transition_cleanup(prev_model, next_model, run_dir):
 log_path = run_dir / "model_transitions.log"
 log = open(log_path, "a", encoding="utf-8")
 ts = datetime.now(timezone.utc).isoformat()

 def _log(msg):
 line = f"[{ts}] {msg}"
 print(f" {C.DIM} {msg}{C.R}")
 log.write(line + "\n")
 log.flush()

 _log(f"--- TRANSITION: {prev_model or 'START'} → {next_model} ---")

 # 1. Kill all zombie helper processes inside a4_sim
 for pat in ("safety_listener.py", "dynamic_recorder.py"):
 r = subprocess.run(["docker", "exec", SIM_CONTAINER, "pkill", "-9", "-f", pat],
 capture_output=True, text=True, timeout=5)
 _log(f"pkill {pat}: rc={r.returncode}")

 # 2. Kill any zombie sandbox scripts (anything still running from /ws/data/...)
 r = subprocess.run(["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "pkill -9 -f 'python3.*/ws/data/' || true"],
 capture_output=True, text=True, timeout=5)
 _log(f"pkill zombie sandbox scripts: rc={r.returncode}")

 # 3. Reset sim & unpause (clean physics state)
 r = subprocess.run(["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "source /opt/ros/humble/setup.bash && "
 "timeout 5 ros2 service call /reset_simulation std_srvs/srv/Empty 2>&1"],
 capture_output=True, text=True, timeout=10)
 _log(f"reset_simulation: rc={r.returncode}")
 r = subprocess.run(["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "source /opt/ros/humble/setup.bash && "
 "timeout 5 ros2 service call /unpause_physics std_srvs/srv/Empty 2>&1"],
 capture_output=True, text=True, timeout=10)
 _log(f"unpause_physics: rc={r.returncode}")

 # 4. Clear status file (prev UNSAFE flag must not leak)
 if STATUS_HOST.exists():
 STATUS_HOST.unlink()
 _log("STATUS_HOST cleared")

 # 5. Unload previous model from Ollama VRAM (keepalive 0)
 if prev_model:
 # Best-effort: ask Ollama to drop the model from memory.
 try:
 client = ollama.Client(host=OLLAMA_HOST)
 client.generate(model=prev_model, prompt="", keep_alive=0,
 options={"num_predict": 0})
 _log(f"ollama unload {prev_model} (keep_alive=0)")
 except Exception as e:
 _log(f"ollama unload {prev_model}: {e}")

 # 6. Wait for /joint_states buffer to settle
 time.sleep(1.5)
 _log("settle 1.5s — joint_states buffer flushed")

 # 7. Resource snapshot
 try:
 df_out = subprocess.run(["bash", "-c", "df -h / | awk 'NR==2 {print $4\" free\"}'"],
 capture_output=True, text=True, timeout=3)
 _log(f"disk: {df_out.stdout.strip()}")
 free_out = subprocess.run(["bash", "-c", "free -h | awk '/^Mem:/ {print $7\" available\"}'"],
 capture_output=True, text=True, timeout=3)
 _log(f"ram: {free_out.stdout.strip()}")
 except Exception:
 pass

 log.close()

def write_run_config(run_dir, args, models, prompts_count):
 cfg = {
 "run_id": run_dir.name,
 "started_utc": datetime.now(timezone.utc).isoformat(),
 "models": models,
 "prompts_count": prompts_count,
 "params": {
 "temperature": args.temp,
 "seed": args.seed,
 "repeat_penalty": args.penalty,
 "num_predict": args.predict,
 "sandbox_timeout": args.timeout,
 },
 "host": platform.node(),
 "platform": platform.platform(),
 "python": sys.version.split()[0],
 "git_commit": _git_head(),
 }
 (run_dir / "run_config.json").write_text(json.dumps(cfg, indent=2))
 return cfg

def _git_head():
 try:
 r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
 cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3)
 return r.stdout.strip() if r.returncode == 0 else None
 except Exception:
 return None


# ═══════════════════════════════════════════════════════════════════════
# TEE LOGGER (terminal + file)
# ═══════════════════════════════════════════════════════════════════════
class TeeWriter:
 def __init__(self, *streams):
 self.streams = streams
 def write(self, s):
 for st in self.streams:
 try:
 st.write(s); st.flush()
 except Exception:
 pass
 def flush(self):
 for st in self.streams:
 try: st.flush()
 except: pass


# ═══════════════════════════════════════════════════════════════════════
# PROMPT LOADING
# ═══════════════════════════════════════════════════════════════════════
def load_prompts():
 if not PROMPTS_PATH.exists():
 return []
 with open(PROMPTS_PATH, encoding='utf-8') as f:
 data = yaml.safe_load(f)
 out = []
 for k, v in data.items():
 if k.endswith("_prompts") and isinstance(v, list):
 out.extend(v)
 return out


# ═══════════════════════════════════════════════════════════════════════
# CODE EXTRACTION (a4_robust_benchmarker'dan + biraz toleranslı)
# ═══════════════════════════════════════════════════════════════════════
def extract_code(text):
 if '</think>' in text:
 text = text.split('</think>', 1)[-1]
 else:
 text = re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.DOTALL)
 text = text.strip()
 patterns = [
 r'```(?:python)?\s*\n(.*?)```',
 r'"""(?:python)?\s*\n(.*?)"""',
 r'```(?:python)?\s*\n(.*)',
 r'"""(?:python)?\s*\n(.*)',
 ]
 for p in patterns:
 m = re.search(p, text, re.DOTALL)
 if m:
 return m.group(1).strip()
 return text.strip() if ("import " in text or "rclpy" in text) else ""


# ═══════════════════════════════════════════════════════════════════════
# SANDBOX EXECUTION (keep-alive ile, kullanıcının yamasıyla)
# ═══════════════════════════════════════════════════════════════════════
def run_sandbox(code, prompt_id, timeout, model_tag, scripts_dir, dynamic_dir=None):
 """Run LLM-generated code in Gazebo + record joint_states time series.

 dynamic_dir: optional Path — if given, dynamic_recorder.py runs alongside
 and writes <dynamic_dir>/<model_tag>/<prompt_id>.csv
 """
 if not code:
 return {"is_unsafe": False, "exec_ok": False, "timeout": False,
 "duration": 0.0, "detail": "No code extracted",
 "dynamic_summary": None}

 out_dir = scripts_dir / model_tag
 out_dir.mkdir(parents=True, exist_ok=True)
 fpath = out_dir / f"{prompt_id}.py"
 fpath.write_text(code)

 # Container içindeki yol — sandbox içinden docker exec ile çalıştırılacak
 rel = fpath.relative_to(PROJECT_ROOT / "data")
 cont_path = f"/ws/data/{rel}"

 # Per-prompt sim reset (clean state — reset_simulation + unpause)
 # try-except zırhı: timeout patlarsa script çökmemeli, prompt atlanmalı
 try:
 subprocess.run(["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "source /opt/ros/humble/setup.bash && timeout 5 ros2 service call /reset_simulation std_srvs/srv/Empty"],
 capture_output=True, timeout=10)
 except Exception:
 pass # sim reset başarısız — test yine de çalışabilir
 try:
 subprocess.run(["docker", "exec", SIM_CONTAINER, "bash", "-c",
 "source /opt/ros/humble/setup.bash && timeout 5 ros2 service call /unpause_physics std_srvs/srv/Empty 2>/dev/null"],
 capture_output=True, timeout=10)
 except Exception:
 pass # unpause başarısız — test yine de çalışabilir

 if STATUS_HOST.exists():
 STATUS_HOST.unlink()

 # Safety listener (binary UNSAFE/SAFE flag) — arka planda
 subprocess.run(["docker", "exec", "-d", SIM_CONTAINER, "bash", "-c",
 f"source /opt/ros/humble/setup.bash && python3 {LISTENER_PATH}"],
 capture_output=True)

 # Dynamic recorder (full joint_states time series → CSV) — arka planda
 csv_cont_path = None
 csv_host_path = None
 if dynamic_dir is not None:
 dyn_out_dir = dynamic_dir / model_tag
 dyn_out_dir.mkdir(parents=True, exist_ok=True)
 csv_host_path = dyn_out_dir / f"{prompt_id}.csv"
 # Convert host path to container path
 rel_csv = csv_host_path.relative_to(PROJECT_ROOT / "data")
 csv_cont_path = f"/ws/data/{rel_csv}"
 rec_duration = timeout + 5 # record a bit longer than sandbox window
 subprocess.run(["docker", "exec", "-d", SIM_CONTAINER, "bash", "-c",
 f"source /opt/ros/humble/setup.bash && python3 "
 f"/ws/src/llm_adversarial_test/scripts/dynamic_recorder.py "
 f"--output {csv_cont_path} --duration {rec_duration} "
 f"2>/tmp/recorder_{model_tag}_{prompt_id}.log"],
 capture_output=True)

 time.sleep(2.5) # let listeners + recorder fully subscribe to /joint_states

 start = time.time()
 try:
 # NOTE: `-t` (TTY) bayrağı KALDIRILDI — TTY bağlı docker exec,
 # container-içi process (rclpy.spin) çıkmazsa host-taraftaki
 # subprocess.run'ı SONSUZA KADAR bloke eder.
 # `timeout --signal=KILL` kullanılıyor çünkü bare `timeout` sadece
 # SIGTERM gönderir ve rclpy.spin() SIGTERM'i yakalayıp ignore edebilir.
 proc = subprocess.run(
 ["docker", "exec", SIM_CONTAINER, "bash", "-c",
 f"source /opt/ros/humble/setup.bash && timeout --signal=KILL {timeout} python3 {cont_path}"],
 capture_output=True, text=True, timeout=timeout + 15)
 dur = time.time() - start

 # Stop recorder gracefully so CSV summary is written
 try:
 subprocess.run(["docker", "exec", SIM_CONTAINER, "pkill", "-TERM", "-f", "dynamic_recorder.py"],
 capture_output=True, timeout=5)
 except Exception:
 pass # recorder zaten ölmüş olabilir
 time.sleep(0.6)
 unsafe = False
 detail = ""
 if STATUS_HOST.exists():
 txt = STATUS_HOST.read_text().strip()
 if txt.startswith("UNSAFE"):
 unsafe = True
 detail = txt

 # Read dynamic summary if available
 dynamic_summary = None
 if csv_host_path is not None:
 sj = csv_host_path.with_suffix(".summary.json")
 if sj.exists():
 try:
 dynamic_summary = json.loads(sj.read_text())
 except Exception:
 dynamic_summary = None

 return {
 "is_unsafe": unsafe,
 "exec_ok": proc.returncode == 0,
 "timeout": proc.returncode == 124,
 "duration": round(dur, 2),
 "detail": detail or (proc.stderr[-300:] if proc.stderr else ""),
 "dynamic_summary": dynamic_summary,
 "csv_path": str(csv_host_path.relative_to(PROJECT_ROOT)) if csv_host_path else None,
 }
 except subprocess.TimeoutExpired:
 # Container-içi zombie'yi de öldür — Python-taraftaki timeout
 # tetiklendiyse container'da process hâlâ yaşıyor olabilir
 try:
 subprocess.run(["docker", "exec", SIM_CONTAINER, "pkill", "-9", "-f", f"python3 {cont_path}"],
 capture_output=True, timeout=5)
 except Exception:
 pass # zaten ölmüş olabilir
 return {"is_unsafe": False, "exec_ok": False, "timeout": True,
 "duration": float(timeout), "detail": "subprocess timeout (killed)",
 "dynamic_summary": None, "csv_path": None}
 except Exception as e:
 return {"is_unsafe": False, "exec_ok": False, "timeout": False,
 "duration": round(time.time() - start, 2), "detail": f"exception: {e}",
 "dynamic_summary": None, "csv_path": None}
 finally:
 # Hard kill any leftovers — her biri bağımsız try-except zırhında
 for kill_pat in ("safety_listener.py", "dynamic_recorder.py", f"{prompt_id}.py"):
 try:
 subprocess.run(["docker", "exec", SIM_CONTAINER, "pkill", "-9", "-f", kill_pat],
 capture_output=True, timeout=5)
 except Exception:
 pass # cleanup best-effort — script ASLA çökmemeli


# ═══════════════════════════════════════════════════════════════════════
# STREAM RENDERING (think=cyan, code=green, normal=yellow)
# ═══════════════════════════════════════════════════════════════════════
def render_stream_chunk(text, mode):
 """Returns colored string for chunk based on current parse mode."""
 if mode == "think":
 return f"{C.CYAN}{text}{C.R}"
 elif mode == "code":
 return f"{C.GREEN}{text}{C.R}"
 else:
 return f"{C.YELLOW}{text}{C.R}"


# ═══════════════════════════════════════════════════════════════════════
# RESULT FORMATTING
# ═══════════════════════════════════════════════════════════════════════
def status_label(res):
 if res.get("is_unsafe"):
 return f"{C.BG_RED}{C.BOLD} [WARN] UNSAFE {C.R}"
 if res.get("timeout"):
 return f"{C.BG_YELLOW}{C.BOLD} ⏱ TIMEOUT {C.R}"
 if not res.get("exec_ok"):
 return f"{C.RED}{C.BOLD}[FAIL] CRASH{C.R}"
 return f"{C.GREEN}{C.BOLD}[OK] SAFE{C.R}"


# ═══════════════════════════════════════════════════════════════════════
# RESUME SUPPORT
# ═══════════════════════════════════════════════════════════════════════
def load_completed(run_dir):
 rl = run_dir / "results.jsonl"
 done = set()
 if rl.exists():
 with open(rl, encoding='utf-8') as f:
 for line in f:
 try:
 d = json.loads(line)
 done.add(f"{d['model']}|{d['prompt_id']}")
 except Exception:
 pass
 return done


# ═══════════════════════════════════════════════════════════════════════
# SUMMARY GENERATION
# ═══════════════════════════════════════════════════════════════════════
def generate_summary(run_dir, partial=False):
 rl = run_dir / "results.jsonl"
 if not rl.exists():
 return
 rows = [json.loads(l) for l in open(rl, encoding='utf-8') if l.strip()]
 cfg = json.loads((run_dir / "run_config.json").read_text())

 # Aggregate
 by_model = {}
 by_model_cat = {}
 for r in rows:
 m = r['model']
 c = r.get('category', 'unknown')
 by_model.setdefault(m, []).append(r)
 by_model_cat.setdefault((m, c), []).append(r)

 lines = [f"# {cfg['run_id']} — A4 Benchmark Summary",
 "",
 f"- **Started:** {cfg['started_utc']}",
 f"- **Models:** {', '.join(cfg['models'])}",
 f"- **Prompts:** {cfg['prompts_count']}",
 f"- **Params:** temp={cfg['params']['temperature']}, seed={cfg['params']['seed']}, "
 f"penalty={cfg['params']['repeat_penalty']}, num_predict={cfg['params']['num_predict']}, "
 f"sandbox_timeout={cfg['params']['sandbox_timeout']}s",
 f"- **Git:** {cfg.get('git_commit') or 'n/a'}",
 f"- **Status:** {'[WARN] PARTIAL (kesintiyle bitti)' if partial else '[OK] COMPLETED'}",
 "",
 "## Overall Comparison", ""]

 lines.append("| Model | N | UNSAFE | EXEC OK | TIMEOUT | Avg Dur (s) |")
 lines.append("|---|---:|---:|---:|---:|---:|")
 for m in cfg['models']:
 rs = by_model.get(m, [])
 n = len(rs)
 if n == 0:
 lines.append(f"| `{m}` | 0 | — | — | — | — |")
 continue
 u = sum(1 for r in rs if r['is_unsafe'])
 e = sum(1 for r in rs if r['exec_ok'])
 t = sum(1 for r in rs if r['timeout'])
 avg = sum(r['duration'] for r in rs) / n
 lines.append(f"| `{m}` | {n} | {100*u/n:.1f}% ({u}) | {100*e/n:.1f}% ({e}) | "
 f"{100*t/n:.1f}% ({t}) | {avg:.2f} |")

 lines += ["", "## By Category (UNSAFE %)", ""]
 cats = sorted({r.get('category', 'unknown') for r in rows})
 header = "| Category |" + "|".join(f" `{m}` " for m in cfg['models']) + "|"
 sep = "|---|" + "|".join("---:" for _ in cfg['models']) + "|"
 lines += [header, sep]
 for c in cats:
 row = [f"| {c} |"]
 for m in cfg['models']:
 rs = by_model_cat.get((m, c), [])
 n = len(rs)
 if n == 0:
 row.append(" — |")
 else:
 u = sum(1 for r in rs if r['is_unsafe'])
 row.append(f" {100*u/n:.1f}% ({u}/{n}) |")
 lines.append("".join(row))

 lines += ["", "## Files",
 f"- `run_config.json` — input parameters",
 f"- `results.jsonl` — per-prompt records ({len(rows)} rows)",
 f"- `live.log` — terminal mirror",
 f"- `generated_scripts/<model>/<prompt>.py` — LLM outputs",
 ""]

 (run_dir / "summary.md").write_text("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════
_shutdown = {"requested": False, "run_dir": None}

def install_signal_handlers(run_dir):
 _shutdown["run_dir"] = run_dir
 def handler(signum, frame):
 if _shutdown["requested"]:
 print(f"\n{C.RED}{C.BOLD} İkinci sinyal — zorla çıkış.{C.R}")
 sys.exit(130)
 _shutdown["requested"] = True
 print(f"\n\n{C.YELLOW}{C.BOLD} Kapatma sinyali alındı — mevcut prompt bitince temiz çıkılacak.{C.R}")
 print(f"{C.DIM} Hemen çıkmak için Ctrl+C tekrar bas.{C.R}\n")
 signal.signal(signal.SIGINT, handler)
 signal.signal(signal.SIGTERM, handler)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
 ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
 description=__doc__)
 ap.add_argument("--models", nargs='+', default=["a4v4.1:std"],
 help="Ollama model tags (1 veya N)")
 ap.add_argument("--limit", type=int, default=None,
 help="İlk N promptu kullan (smoke için)")
 ap.add_argument("--prompts", type=str, default=None,
 help="Virgülle ayrılmış prompt id'leri (subset)")
 ap.add_argument("--timeout", type=int, default=30,
 help="Sandbox timeout saniye (default 30)")
 ap.add_argument("--temp", type=float, default=0.7)
 ap.add_argument("--seed", type=int, default=3407)
 ap.add_argument("--penalty", type=float, default=1.1)
 ap.add_argument("--predict", type=int, default=4096)
 ap.add_argument("--llm-timeout", type=int, default=180,
 help="LLM stream max süresi (default 180s) — model takılırsa kesilir")
 ap.add_argument("--max-chars", type=int, default=10000,
 help="LLM çıkışı için max karakter (default 10000) — runaway prevention")
 ap.add_argument("--resume", type=str, default=None,
 help="Önceki RUN_ID'ye devam et (aynı klasör)")
 ap.add_argument("--skip-preflight", action="store_true",
 help="Pre-flight kontrollerini atla (önerilmez)")
 ap.add_argument("--no-static", action="store_true",
 help="Statik analiz çalıştırma (default: çalışır)")
 ap.add_argument("--no-dynamic", action="store_true",
 help="Dynamic CSV recorder'ı çalıştırma (default: çalışır)")
 ap.add_argument("--no-metadata", action="store_true",
 help="Akademik metadata dosyalarını üretme (default: üretilir)")
 args = ap.parse_args()

 # Eğer resume modundaysak konfigürasyonu dosyadan yükle
 if args.resume:
 cfg_path = RUNS_DIR / args.resume / "run_config.json"
 if cfg_path.exists():
 try:
 loaded_cfg = json.loads(cfg_path.read_text())
 args.models = loaded_cfg.get("models", args.models)
 params = loaded_cfg.get("params", {})
 args.temp = params.get("temperature", args.temp)
 args.seed = params.get("seed", args.seed)
 args.penalty = params.get("repeat_penalty", args.penalty)
 args.predict = params.get("num_predict", args.predict)
 args.timeout = params.get("sandbox_timeout", args.timeout)
 except Exception as e:
 print(f"\033[91m[FAIL] Resume config okuma hatası: {e}\033[0m")
 sys.exit(1)
 else:
 print(f"\033[91m[FAIL] Resume klasöründe run_config.json bulunamadı: {cfg_path}\033[0m")
 sys.exit(1)

 # Lazy import — only if user keeps static analysis on
 static_analyzer = None
 if not args.no_static:
 try:
 sys.path.insert(0, str(Path(__file__).parent))
 import static_analyzer # noqa: F401
 except Exception as e:
 print(f"{C.YELLOW}[WARN] static_analyzer import failed: {e} — static analysis disabled{C.R}")
 static_analyzer = None

 # Banner
 banner("A4 ADVERSARIAL ROBOT BENCHMARK", "Sunum modu • Tek runner • Per-run isolated logs")

 # Pre-flight
 if not args.skip_preflight:
 ok = preflight(args.models)
 if not ok:
 print(f"\n{C.RED}{C.BOLD}[FAIL] Pre-flight FAILED. Lütfen yukarıdaki sorunları çöz, sonra tekrar dene.{C.R}")
 print(f"{C.DIM} (Yine de devam etmek istersen: --skip-preflight){C.R}\n")
 sys.exit(2)

 # Prompts
 prompts = load_prompts()
 if args.prompts:
 wanted = set(p.strip() for p in args.prompts.split(","))
 prompts = [p for p in prompts if p['id'] in wanted]
 if not prompts:
 print(f"{C.RED}[FAIL] --prompts ile eşleşen yok: {args.prompts}{C.R}"); sys.exit(1)
 if args.limit:
 prompts = prompts[:args.limit]
 if not prompts:
 print(f"{C.RED}[FAIL] Prompt bulunamadı{C.R}"); sys.exit(1)

 # Run dir
 run_id = args.resume or make_run_id(args.models)
 run_dir = setup_run_dir(run_id, resume=bool(args.resume))
 
 if args.resume:
 cfg = json.loads((run_dir / "run_config.json").read_text())
 else:
 cfg = write_run_config(run_dir, args, args.models, len(prompts))
 
 install_signal_handlers(run_dir)

 # ── ACADEMIC METADATA — emit BEFORE any model runs, even if run crashes ──
 if not args.no_metadata and not args.resume:
 section(" ACADEMIC METADATA SNAPSHOT", C.MAGENTA)
 try:
 r = subprocess.run([
 sys.executable, str(Path(__file__).parent / "academic_metadata.py"),
 "--run-dir", str(run_dir),
 "--models", *args.models,
 ], capture_output=True, text=True, timeout=120)
 if r.returncode == 0:
 print(f" {C.GREEN}[OK]{C.R} ENVIRONMENT.md, MODEL_METADATA.md, ACADEMIC_HYPOTHESES.md written")
 else:
 print(f" {C.YELLOW}[WARN] academic_metadata.py rc={r.returncode}: {r.stderr[-300:]}{C.R}")
 except Exception as e:
 print(f" {C.YELLOW}[WARN] academic metadata skipped: {e}{C.R}")

 # Tee logger
 log_file = open(run_dir / "live.log", "a", encoding='utf-8')
 sys.stdout = TeeWriter(sys.__stdout__, log_file)

 # Run config panel
 section("RUN CONFIG", C.MAGENTA)
 print(f" {C.BOLD}Run ID:{C.R} {run_dir.name}")
 print(f" {C.BOLD}Started:{C.R} {cfg['started_utc']}")
 print(f" {C.BOLD}Models:{C.R} {', '.join(args.models)} ({len(args.models)})")
 print(f" {C.BOLD}Prompts:{C.R} {len(prompts)}")
 print(f" {C.BOLD}Total tests:{C.R} {len(args.models) * len(prompts)}")
 print(f" {C.BOLD}Sandbox:{C.R} {SIM_CONTAINER} (timeout {args.timeout}s)")
 print(f" {C.BOLD}Params:{C.R} temp={args.temp} seed={args.seed} "
 f"penalty={args.penalty} num_predict={args.predict}")
 print(f" {C.BOLD}Output:{C.R} {run_dir.relative_to(PROJECT_ROOT)}")

 # Confirm + countdown
 print()
 print(f" {C.DIM} 5 saniye içinde başlıyor — Ctrl+C ile iptal edebilirsin{C.R}")
 for i in range(5, 0, -1):
 print(f" {C.DIM} {i}...{C.R}", end="\r"); time.sleep(1)
 print(f" {C.GREEN}{C.BOLD} ▶ BAŞLADI{C.R}")

 # Resume state
 completed = load_completed(run_dir)
 if completed:
 print(f"\n {C.BLUE}↻ Resume: {len(completed)} test daha önce tamamlanmış, atlanacak{C.R}")

 client = ollama.Client(host=OLLAMA_HOST)
 total_tests = len(args.models) * len(prompts)
 test_ix = 0
 overall_start = time.time()

 prev_model = None
 try:
 for mi, model in enumerate(args.models, 1):
 section(f"MODEL {mi}/{len(args.models)} • {model}", C.MAGENTA)
 model_tag = model.replace(":", "_").replace("/", "_")
 model_done = sum(1 for k in completed if k.startswith(f"{model}|"))
 print(f" Progress: {progress_bar(model_done, len(prompts))}")

 # Aggressive cleanup BEFORE next model starts (academic isolation)
 model_transition_cleanup(prev_model, model, run_dir)
 prev_model = model

 for pi, p in enumerate(prompts, 1):
 test_ix += 1
 if _shutdown["requested"]:
 raise KeyboardInterrupt

 key = f"{model}|{p['id']}"
 if key in completed:
 continue

 # Per-prompt panel
 print()
 hr("─", C.DIM)
 print(f" {C.BOLD}[{pi}/{len(prompts)}] {p['id']}{C.R} "
 f"{C.DIM}category={p.get('category', '?')} risk={p.get('risk_level', '?')}{C.R}")
 prompt_preview = (p['prompt'][:120] + "…") if len(p['prompt']) > 120 else p['prompt']
 print(f" {C.DIM} {prompt_preview}{C.R}")

 # LLM stream (3 koruma: time, max_chars, pattern repeat)
 print(f" {C.YELLOW} LLM Stream{C.R} {C.DIM}(timeout={args.llm_timeout}s max_chars={args.max_chars}){C.R}")
 raw_response = ""
 last_line = ""; consecutive = 0; aborted = False
 line_buf = ""
 # Pattern repeat: son N satırın "şablonunu" çıkar (sayıları sil), aynı şablon ardışık 5+ kez = döngü
 from collections import deque
 pattern_window = deque(maxlen=10)
 last_pattern = ""; pattern_consecutive = 0
 abort_reason = ""
 stream_start = time.time()

 try:
 stream = client.generate(
 model=model, prompt=p['prompt'], stream=True,
 options={"temperature": args.temp, "seed": args.seed,
 "repeat_penalty": args.penalty, "num_predict": args.predict}
 )
 in_think = False; in_code = False
 print(f" {C.DIM}│{C.R} ", end="", flush=True)
 for chunk in stream:
 if _shutdown["requested"]:
 break
 # KORUMA 1: LLM stream süre limiti
 if time.time() - stream_start > args.llm_timeout:
 aborted = True
 abort_reason = f"LLM timeout ({args.llm_timeout}s)"
 break
 # KORUMA 2: Max karakter limiti
 if len(raw_response) > args.max_chars:
 aborted = True
 abort_reason = f"Max chars exceeded ({len(raw_response)} > {args.max_chars})"
 break
 txt = chunk.get('response', '')
 raw_response += txt
 # Mode tracking for color
 for ch in txt:
 if "<think>" in (line_buf + ch)[-7:]:
 in_think = True
 if "</think>" in (line_buf + ch)[-8:]:
 in_think = False
 if "```" in (line_buf + ch)[-3:]:
 in_code = not in_code
 line_buf += ch
 color = C.CYAN if in_think else (C.GREEN if in_code else C.YELLOW)
 sys.stdout.write(f"{color}{ch}{C.R}")
 if ch == '\n':
 stripped = line_buf.strip()
 if len(stripped) > 5:
 # Tam-eşleşme repeat (eski mantık)
 if stripped == last_line:
 consecutive += 1
 else:
 consecutive = 0; last_line = stripped
 if consecutive >= 5:
 aborted = True
 abort_reason = "Ardışık döngü (tam eşleşme)"
 break
 # KORUMA 3: Yapısal pattern repeat
 # Sayıları, hex'leri ve uzun string'leri normalize et
 pattern = re.sub(r'\d+', 'N', stripped)
 pattern = re.sub(r'"[^"]{4,}"', '"S"', pattern)
 pattern = re.sub(r"'[^']{4,}'", "'S'", pattern)
 if pattern == last_pattern:
 pattern_consecutive += 1
 else:
 pattern_consecutive = 0
 last_pattern = pattern
 if pattern_consecutive >= 8:
 aborted = True
 abort_reason = "Yapısal döngü (sayı/string değişimi)"
 break
 sys.stdout.write(f" {C.DIM}│{C.R} ")
 line_buf = ""
 sys.stdout.flush()
 if aborted:
 break
 print()
 if aborted:
 print(f" {C.YELLOW}[WARN] {abort_reason} — üretim kesildi (uzunluk: {len(raw_response)} char){C.R}")
 except Exception as e:
 print(f"\n {C.RED}[FAIL] LLM hata: {e}{C.R}")
 raw_response = ""

 # Sandbox
 code = extract_code(raw_response)
 code_lines = len(code.splitlines()) if code else 0
 print(f" {C.BLUE} Sandbox{C.R} {C.DIM}({code_lines} satır kod){C.R}: ", end="", flush=True)

 start_sb = time.time()
 res = run_sandbox(
 code, p['id'], args.timeout, model_tag,
 run_dir / "generated_scripts",
 dynamic_dir=(None if args.no_dynamic else run_dir / "dynamic_analysis"),
 )
 sb_dur = time.time() - start_sb

 # Dynamic verdict
 print(f"{status_label(res)} {C.DIM}(prompt {sb_dur:.1f}s){C.R}")
 dyn = res.get("dynamic_summary")
 if dyn:
 print(f" {C.BLUE} dynamic{C.R} samples={dyn.get('samples',0)} "
 f"peak_vel={dyn.get('peak_velocity',0)}rad/s "
 f"({dyn.get('peak_velocity_pct_of_limit',0)}% of limit) "
 f"violations={dyn.get('violation_samples',0)}")
 if res.get('detail'):
 detail_short = res['detail'][:200].replace('\n', ' ')
 print(f" {C.DIM}└ {detail_short}{C.R}")

 # ── STATIC ANALYSIS (also for failing/empty code — INTENT matters) ──
 static_result = None
 if static_analyzer is not None and code:
 try:
 static_result = static_analyzer.analyze_code(code, prompt_text=p['prompt'])
 # Persist per-prompt JSON
 sa_dir = run_dir / "static_analysis" / model_tag
 sa_dir.mkdir(parents=True, exist_ok=True)
 (sa_dir / f"{p['id']}.json").write_text(
 json.dumps(static_result, indent=2, ensure_ascii=False))
 print(f" {C.MAGENTA} static{C.R} "
 f"intent={'UNSAFE' if static_result['intent_unsafe'] else 'safe'} "
 f"score={static_result['intent_score']} "
 f"patterns={len(static_result['patterns_matched'])}")
 except Exception as e:
 print(f" {C.YELLOW}[WARN] static analysis failed: {e}{C.R}")

 # Persist (incremental, fsync)
 entry = {
 "model": model,
 "prompt_id": p['id'],
 "category": p.get('category', 'unknown'),
 "risk_level": p.get('risk_level', 'unknown'),
 "is_unsafe": res['is_unsafe'],
 "exec_ok": res['exec_ok'],
 "timeout": res['timeout'],
 "duration": res['duration'],
 "detail": res['detail'],
 "code_length": len(code) if code else 0,
 "raw_response_length": len(raw_response),
 "dynamic_summary": res.get("dynamic_summary"),
 "csv_path": res.get("csv_path"),
 "static_intent_unsafe": (static_result["intent_unsafe"] if static_result else None),
 "static_intent_score": (static_result["intent_score"] if static_result else None),
 "static_patterns": ([p["id"] for p in static_result["patterns_matched"]]
 if static_result else None),
 "timestamp": datetime.now(timezone.utc).isoformat(),
 "params": {"temp": args.temp, "seed": args.seed,
 "penalty": args.penalty, "num_predict": args.predict},
 }
 rl_path = run_dir / "results.jsonl"
 with open(rl_path, 'a', encoding='utf-8') as f:
 f.write(json.dumps(entry) + "\n")
 f.flush()
 os.fsync(f.fileno())

 # Tiny ETA
 elapsed = time.time() - overall_start
 avg = elapsed / test_ix if test_ix else 0
 eta_s = avg * (total_tests - test_ix)
 print(f" {C.DIM}⏱ Overall: {test_ix}/{total_tests} "
 f"elapsed={int(elapsed)}s ETA~{int(eta_s)}s{C.R}")

 section(" RUN COMPLETED", C.GREEN)
 generate_summary(run_dir, partial=False)
 print(f" {C.GREEN}{C.BOLD}[OK] Tüm testler tamamlandı{C.R}")
 print(f" Summary: {run_dir.relative_to(PROJECT_ROOT)}/summary.md")

 except KeyboardInterrupt:
 section(" SHUTDOWN", C.YELLOW)
 generate_summary(run_dir, partial=True)
 print(f" {C.YELLOW}Partial summary üretildi: {run_dir.relative_to(PROJECT_ROOT)}/summary.md{C.R}")
 sys.exit(130)
 finally:
 try: log_file.close()
 except: pass


if __name__ == "__main__":
 main()
