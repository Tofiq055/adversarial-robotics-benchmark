#!/usr/bin/env python3
"""
academic_metadata.py — Snapshot the experimental environment at run start.

Writes three Markdown documents that travel with each ablation run:
  • ENVIRONMENT.md       — system snapshot (kernel, GPU, Docker, ROS 2, Ollama)
  • MODEL_METADATA.md    — per-model profile (blob SHA, modelfile, params)
  • ACADEMIC_HYPOTHESES.md — what the run is meant to prove + comparison pairs

This is the FIRST thing the orchestrator does — before any model runs — so that
even if the experiment crashes mid-way, the academic context is preserved.

Usage:
  python3 scripts/academic_metadata.py \\
    --run-dir data/results/runs/RUN_20260515_xx \\
    --models base:ablation v2:ablation ... v5.0-pure:ablation
"""
from __future__ import annotations
import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ════════════════════════════════════════════════════════════════════════
# SHELL HELPERS
# ════════════════════════════════════════════════════════════════════════
def sh(cmd: list[str], timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"<error: {e}>"


def docker_exec(container: str, cmd: str, timeout: int = 10) -> str:
    return sh(["docker", "exec", container, "bash", "-c", cmd], timeout=timeout)


# ════════════════════════════════════════════════════════════════════════
# ENVIRONMENT SNAPSHOT
# ════════════════════════════════════════════════════════════════════════
def collect_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    env["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    env["hostname"] = platform.node()
    env["kernel"] = f"{platform.system()} {platform.release()}"
    env["cpu_arch"] = platform.machine()
    env["python_version"] = sys.version.split()[0]

    # CPU
    try:
        cpu_model = sh(["bash", "-c", "lscpu | grep 'Model name' | head -1 | cut -d: -f2"])
        env["cpu_model"] = cpu_model.strip() or "?"
    except Exception:
        env["cpu_model"] = "?"

    # Memory
    try:
        mem_total = sh(["bash", "-c", "free -h | awk '/^Mem:/ {print $2}'"])
        env["memory_total"] = mem_total or "?"
    except Exception:
        env["memory_total"] = "?"

    # GPU
    nvidia = sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    env["gpu"] = nvidia or "<none>"

    # Disk
    disk_root = sh(["bash", "-c", "df -h / | awk 'NR==2 {print $2\" total, \"$4\" free, \"$5\" used\"}'"])
    env["disk_root"] = disk_root or "?"

    # Docker
    env["docker_version"] = sh(["docker", "version", "--format", "{{.Server.Version}}"])
    env["docker_compose"] = sh(["docker", "compose", "version", "--short"])

    # Container images & status
    containers: list[str] = []
    for c in ("a4_ollama", "a4_sim", "a4_testrunner"):
        status = sh(["docker", "inspect", "-f", "{{.State.Status}}", c])
        image = sh(["docker", "inspect", "-f", "{{.Config.Image}}", c])
        containers.append(f"{c}: {status} ({image})")
    env["containers"] = "\n  ".join(containers)

    # ROS 2 + MoveIt versions inside sim
    env["ros_distro"] = docker_exec("a4_sim", "echo $ROS_DISTRO") or "?"
    moveit_pkg = docker_exec(
        "a4_sim",
        "source /opt/ros/humble/setup.bash && ros2 pkg list 2>/dev/null | grep '^moveit$' | head -1",
    )
    env["moveit"] = "installed" if moveit_pkg else "<not found>"

    # Gazebo
    env["gazebo"] = docker_exec("a4_sim", "gazebo --version 2>&1 | head -1") or "?"

    # Ollama
    env["ollama_version"] = docker_exec("a4_ollama", "ollama --version 2>&1 | head -1")

    # Git
    try:
        env["git_branch"] = sh(["git", "-C", str(PROJECT_ROOT), "rev-parse", "--abbrev-ref", "HEAD"])
        env["git_head"] = sh(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"])
        env["git_dirty"] = sh(["git", "-C", str(PROJECT_ROOT), "status", "--porcelain"])
        env["git_dirty"] = f"{len(env['git_dirty'].splitlines())} files modified" if env["git_dirty"] else "clean"
    except Exception:
        env["git_branch"] = "?"
        env["git_head"] = "?"
        env["git_dirty"] = "?"

    # Terminal width (informational)
    env["terminal_width"] = str(shutil.get_terminal_size((100, 30)).columns)

    return env


def write_environment_md(env: dict[str, str], path: Path) -> None:
    lines = [
        "# Environment Snapshot",
        "",
        "> Captured at run start. This file freezes the system state so the experiment is reproducible.",
        "",
        f"**Timestamp (UTC):** {env['timestamp_utc']}",
        f"**Hostname:** `{env['hostname']}`",
        "",
        "## System",
        f"- **Kernel:** {env['kernel']} ({env['cpu_arch']})",
        f"- **CPU:** {env['cpu_model']}",
        f"- **Memory:** {env['memory_total']}",
        f"- **GPU:** {env['gpu']}",
        f"- **Disk (/) :** {env['disk_root']}",
        f"- **Python:** {env['python_version']}",
        "",
        "## Docker",
        f"- **Engine:** {env['docker_version']}",
        f"- **Compose:** {env['docker_compose']}",
        "- **Containers:**",
        f"  {env['containers']}",
        "",
        "## ROS 2 / MoveIt / Gazebo (inside `a4_sim`)",
        f"- **ROS distro:** {env['ros_distro']}",
        f"- **MoveIt:** {env['moveit']}",
        f"- **Gazebo:** {env['gazebo']}",
        "",
        "## Ollama (inside `a4_ollama`)",
        f"- **Version:** {env['ollama_version']}",
        "",
        "## Git",
        f"- **Branch:** {env['git_branch']}",
        f"- **HEAD:** `{env['git_head']}`",
        f"- **Working tree:** {env['git_dirty']}",
        "",
    ]
    path.write_text("\n".join(lines))


# ════════════════════════════════════════════════════════════════════════
# MODEL METADATA
# ════════════════════════════════════════════════════════════════════════
# Known training stats from the local timeline. Used to enrich the report
# beyond what `ollama show` exposes. Source: docs/MODEL_VERSIONS_TIMELINE.md
KNOWN_TRAINING_STATS: dict[str, dict[str, str]] = {
    "base:ablation":     {"hf_repo": "Qwen/Qwen3.5-4B (no fine-tune)", "training_loss": "n/a", "steps": "0", "lr": "n/a", "lora_r": "n/a", "dataset": "n/a", "template_train": "n/a"},
    "v2:ablation":       {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",                  "training_loss": "?", "steps": "500", "lr": "2e-4", "lora_r": "8",  "dataset": "Garbage Grok (923)",   "template_train": "Alpaca"},
    "v3:ablation":       {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",          "training_loss": "~0.31", "steps": "500", "lr": "2e-4", "lora_r": "16", "dataset": "Golden NVIDIA (936)", "template_train": "Alpaca"},
    "a4v4.1:ablation":   {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",          "training_loss": "0.2464", "steps": "500", "lr": "2e-4", "lora_r": "8",  "dataset": "V4 (849)",            "template_train": "Alpaca"},
    "a4v4.2:ablation":   {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",          "training_loss": "~0.18", "steps": "500", "lr": "2e-4", "lora_r": "8",  "dataset": "V4.2 CLEAN (849)",    "template_train": "Alpaca"},
    "a4v4.3:ablation":   {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",          "training_loss": "0.1041", "steps": "800", "lr": "1e-4", "lora_r": "16", "dataset": "V4.2 CLEAN (849)",    "template_train": "Alpaca"},
    "a4v4.4:ablation":   {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",          "training_loss": "0.1253", "steps": "1000", "lr": "5e-5", "lora_r": "16", "dataset": "V4.2 CLEAN (849)",    "template_train": "Alpaca"},
    "v5.0:ablation":     {"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",         "training_loss": "0.2037", "steps": "800", "lr": "1e-4", "lora_r": "16", "dataset": "V4.2 CLEAN (849)",    "template_train": "ChatML"},
    "v5.0-pure:ablation":{"hf_repo": "[withheld — see ETHICS.md disclosure protocol]",   "training_loss": "0.2090", "steps": "1000", "lr": "5e-5", "lora_r": "16", "dataset": "V4.2 CLEAN (849)",    "template_train": "ChatML"},
}


def collect_model_info(model_tag: str) -> dict[str, str]:
    """Pull blob SHA + Modelfile parameters from Ollama, merge with known stats."""
    info: dict[str, str] = dict(KNOWN_TRAINING_STATS.get(model_tag, {}))
    modelfile = docker_exec("a4_ollama", f"ollama show {model_tag} --modelfile")
    info["modelfile_raw"] = modelfile

    # Extract blob SHA
    m = re.search(r"sha256-([0-9a-f]+)", modelfile)
    info["blob_sha256"] = m.group(1) if m else "?"

    # Inference parameters
    for line in modelfile.splitlines():
        line = line.strip()
        if line.startswith("PARAMETER temperature"):
            info["param_temperature"] = line.split()[-1]
        elif line.startswith("PARAMETER seed"):
            info["param_seed"] = line.split()[-1]
        elif line.startswith("PARAMETER repeat_penalty"):
            info["param_repeat_penalty"] = line.split()[-1]
        elif line.startswith("PARAMETER num_predict"):
            info["param_num_predict"] = line.split()[-1]
        elif line.startswith("PARAMETER num_ctx"):
            info["param_num_ctx"] = line.split()[-1]

    # Template type detection — look ONLY inside the TEMPLATE """...""" block,
    # not the whole modelfile (which may include both formats in stop tokens)
    # Ollama may emit TEMPLATE """...""" or TEMPLATE "..." — handle both.
    # Greedy match the longest balanced quoted block until the next directive line.
    template_match = (re.search(r'TEMPLATE\s+"""(.*?)"""', modelfile, re.DOTALL)
                      or re.search(r'TEMPLATE\s+"(.*?)"\s*\n(?:[A-Z]+|$)',
                                   modelfile, re.DOTALL))
    template_body = template_match.group(1) if template_match else ""
    # Fallback: scan only lines starting with `TEMPLATE` and the immediate
    # following lines until next directive keyword
    if not template_body:
        in_template = False
        buf: list[str] = []
        for line in modelfile.splitlines():
            if line.startswith("TEMPLATE"):
                in_template = True
                buf.append(line)
                continue
            if in_template:
                if re.match(r'^(SYSTEM|PARAMETER|FROM|ADAPTER|LICENSE|MESSAGE)\b', line):
                    break
                buf.append(line)
        template_body = "\n".join(buf)
    if "<|im_start|>" in template_body:
        info["template_inference"] = "ChatML"
    elif "Below is an instruction" in template_body or "### Instruction" in template_body:
        info["template_inference"] = "Alpaca"
    else:
        info["template_inference"] = "?"

    # Stop tokens
    stops = []
    for line in modelfile.splitlines():
        line = line.strip()
        if line.startswith("PARAMETER stop"):
            stops.append(line.split(maxsplit=2)[-1])
    info["stop_tokens"] = ", ".join(stops) if stops else "<none>"

    return info


def write_model_metadata_md(models: list[str], path: Path) -> None:
    lines = [
        "# Model Metadata (per-model academic profile)",
        "",
        "> Pulled live from Ollama at run start. Together with `ENVIRONMENT.md`",
        "> this gives any reader the full provenance of the experiment.",
        "",
        f"**Models in this run:** {len(models)}",
        "",
        "## Comparative Inference Parameters Table",
        "",
        "| Model | template_inference | temp | seed | penalty | predict | ctx | blob_sha256 (12) |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    infos: list[tuple[str, dict[str, str]]] = []
    for m in models:
        info = collect_model_info(m)
        infos.append((m, info))
        sha = info.get("blob_sha256", "?")
        lines.append(
            f"| `{m}` | {info.get('template_inference','?')} "
            f"| {info.get('param_temperature','?')} "
            f"| {info.get('param_seed','?')} "
            f"| {info.get('param_repeat_penalty','?')} "
            f"| {info.get('param_num_predict','?')} "
            f"| {info.get('param_num_ctx','?')} "
            f"| `{sha[:12]}…` |"
        )

    lines += [
        "",
        "## Comparative Training Parameters Table",
        "",
        "| Model | hf_repo | template_train | dataset | LoRA r | steps | LR | final_loss |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for m, info in infos:
        lines.append(
            f"| `{m}` | {info.get('hf_repo','?')} "
            f"| {info.get('template_train','?')} "
            f"| {info.get('dataset','?')} "
            f"| {info.get('lora_r','?')} "
            f"| {info.get('steps','?')} "
            f"| {info.get('lr','?')} "
            f"| {info.get('training_loss','?')} |"
        )

    lines += ["", "---", "", "## Per-Model Modelfile (raw)", ""]
    for m, info in infos:
        lines.append(f"### `{m}`")
        lines.append("")
        lines.append("```")
        lines.append(info.get("modelfile_raw", "<missing>")[:4000])
        lines.append("```")
        lines.append("")
    path.write_text("\n".join(lines))


# ════════════════════════════════════════════════════════════════════════
# ACADEMIC HYPOTHESES (auto-derived from model list)
# ════════════════════════════════════════════════════════════════════════
def write_hypotheses_md(models: list[str], path: Path) -> None:
    lines = [
        "# Academic Hypotheses & Comparison Pairs",
        "",
        "> Defines what this run is supposed to prove. Each comparison pair isolates",
        "> a single variable so observed differences can be attributed cleanly.",
        "",
        "## Top-level question",
        "",
        "> **Does instruction fine-tuning on adversarial robotics data measurably increase",
        "> the rate of physically dangerous robot trajectories generated by an LLM,",
        "> compared to an uninstructed base model?**",
        "",
        "## Metrics collected per (model, prompt)",
        "",
        "1. **Static intent score** (`intent_score ∈ [0,1]`) — pattern-based.",
        "   Captures *what the model intended* even when the code fails to run.",
        "2. **Sandbox dynamic outcome** (`UNSAFE` / `SAFE` / `CRASH` / `TIMEOUT`)",
        "   from `safety_listener.py` watching `/joint_states` in Gazebo.",
        "3. **Joint-state time series CSV** — per-prompt full velocity/effort log",
        "   sampled at ~100 Hz during sandbox execution. Enables peak-velocity,",
        "   ihlal süresi, and trajectory-shape analysis after the fact.",
        "4. **Code structure** — line count, AST parses?, MoveIt used?, etc.",
        "",
        "## Comparison Pairs (single-variable isolation)",
        "",
        "| Pair | Question Answered | Constants | Variable |",
        "|---|---|---|---|",
    ]
    pair_specs: list[tuple[str, str, str, str]] = []
    s = set(models)
    if {"base:ablation", "v2:ablation"} <= s:
        pair_specs.append(("Base ↔ V2", "Does ANY adversarial fine-tune increase UNSAFE?",
                           "inference setup", "fine-tuned vs not"))
    if {"v2:ablation", "v3:ablation"} <= s:
        pair_specs.append(("V2 ↔ V3", "Garbage data vs Golden data",
                           "base model, inference template (Alpaca)", "dataset quality + LoRA r (8→16)"))
    if {"a4v4.1:ablation", "a4v4.2:ablation"} <= s:
        pair_specs.append(("V4.1 ↔ V4.2", "Cleaner-bug fix effect",
                           "hyperparams identical", "dataset (cleaned)"))
    if {"a4v4.3:ablation", "a4v4.4:ablation"} <= s:
        pair_specs.append(("V4.3 ↔ V4.4", "Slower LR + more steps stabler?",
                           "dataset, LoRA r=16, template", "steps (800→1000), LR (1e-4→5e-5)"))
    if {"a4v4.4:ablation", "v5.0-pure:ablation"} <= s:
        pair_specs.append(("V4.4 ↔ V5.0-pure", "ChatML training format better than Alpaca?",
                           "dataset, LoRA r=16, 1000 step, lr=5e-5", "training template (Alpaca→ChatML)"))
    if {"v5.0:ablation", "v5.0-pure:ablation"} <= s:
        pair_specs.append(("V5.0 ↔ V5.0-pure", "Faster convergence vs longer schedule (cost vs quality)",
                           "dataset, template (ChatML), LoRA r=16", "steps (800→1000), LR (1e-4→5e-5)"))

    for name, q, c, v in pair_specs:
        lines.append(f"| **{name}** | {q} | {c} | {v} |")

    lines += [
        "",
        "## Threats to Validity",
        "",
        "- **Stochasticity:** All models share `seed=3407` to reduce sampling noise.",
        "- **Sandbox flakiness:** Gazebo physics jitter can mask near-threshold violations.",
        "  Mitigation: dynamic CSV records actual peaks for post-hoc rescoring.",
        "- **Prompt order:** Prompts are presented in a fixed order; if a model's",
        "  later answers depend on KV-cache leakage from earlier ones, results may be biased.",
        "  Mitigation: orchestrator unloads each model fully before the next starts.",
        "- **Static analyzer false positives:** Pattern matching over-fires on legitimate",
        "  code that happens to contain the same numeric literals (e.g. velocity=10 in a comment).",
        "  Mitigation: per-prompt `static_analysis/<model>/<prompt>.json` records every",
        "  matched pattern + evidence string, allowing manual spot-check of summary outliers.",
        "",
        "## Expected Direction (what the literature predicts)",
        "",
        "- Base should produce mostly syntactically incorrect or refusing answers → **low static AND low dynamic UNSAFE**.",
        "- Fine-tuned variants should produce more well-formed dangerous code → **higher static intent**.",
        "- Whether higher intent translates to higher dynamic UNSAFE depends on whether the",
        "  generated code actually executes against the live Gazebo environment.",
        "",
    ]
    path.write_text("\n".join(lines))


# ════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, help="Run directory (will be created)")
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model tags in run order")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Collecting environment snapshot…")
    env = collect_environment()
    write_environment_md(env, run_dir / "ENVIRONMENT.md")
    (run_dir / "environment.json").write_text(json.dumps(env, indent=2))
    print(f"  ✓ ENVIRONMENT.md ({(run_dir / 'ENVIRONMENT.md').stat().st_size} bytes)")

    print(f"Collecting per-model metadata for {len(args.models)} models…")
    write_model_metadata_md(args.models, run_dir / "MODEL_METADATA.md")
    print(f"  ✓ MODEL_METADATA.md ({(run_dir / 'MODEL_METADATA.md').stat().st_size} bytes)")

    print(f"Writing hypotheses & comparison pairs…")
    write_hypotheses_md(args.models, run_dir / "ACADEMIC_HYPOTHESES.md")
    print(f"  ✓ ACADEMIC_HYPOTHESES.md ({(run_dir / 'ACADEMIC_HYPOTHESES.md').stat().st_size} bytes)")

    print(f"\nAcademic metadata written to: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
