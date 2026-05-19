#!/usr/bin/env python3
"""
Automated Dataset Refurbishment Pipeline
=========================================
Processes a raw dataset through the following pipeline:
1. Static validation (syntax, imports, joint names)
2. Gazebo sandbox execution test
3. If crash/timeout occurs -> Auto-fix via LLM API (max 3 attempts)
4. Saves successful scripts to the new dataset

Usage:
    python3 scripts/dataset_auto_fix.py                           # Full run
    python3 scripts/dataset_auto_fix.py --dry-run                  # API/Gazebo skipped, static only
    python3 scripts/dataset_auto_fix.py --range 0 50               # Process entries 0-49
    python3 scripts/dataset_auto_fix.py --skip-gazebo              # Static + fix, skip Gazebo
"""
import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Paths ──
PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DATASET = PROJECT_ROOT / "data" / "input_dataset.jsonl"
OUTPUT_DATASET = PROJECT_ROOT / "data" / "refined_dataset.jsonl"
FIX_LOG = PROJECT_ROOT / "data" / "fix_log.jsonl"
UNFIXABLE_FILE = PROJECT_ROOT / "data" / "unfixable_dataset.jsonl"
SCRIPTS_DIR = PROJECT_ROOT / "data" / "scripts_out"
SIM_CONTAINER = "a4_sim"
GAZEBO_TIMEOUT = 45

# ── LLM API ──
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "generic-reasoning-model-fast")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

# ── Correct UR5e Joint Names ──
CORRECT_JOINTS = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
]

MAX_FIX_ATTEMPTS = 3


# ═══════════════════════════════════════════════════════
# 1. DATASET LOADER
# ═══════════════════════════════════════════════════════

def load_dataset(path: Path) -> list:
    """Load JSONL dataset. Each line is an entry."""
    entries = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ═══════════════════════════════════════════════════════
# 2. SCRIPT EXTRACTOR
# ═══════════════════════════════════════════════════════

def extract_script_to_file(entry: dict, index: int, output_dir: Path) -> Path:
    """Save response from dataset entry as a .py file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"script_{index:04d}.py"
    filepath = output_dir / filename
    filepath.write_text(entry["response"], encoding="utf-8")
    return filepath


# ═══════════════════════════════════════════════════════
# 3. STATIC VALIDATION
# ═══════════════════════════════════════════════════════

def static_validate(code: str) -> dict:
    """Static analysis of code quality."""
    result = {
        "syntax_ok": False,
        "has_rclpy": False,
        "has_fjt": False,
        "has_main": False,
        "has_correct_joints": False,
        "has_wrong_joints": False,
        "has_shutdown": False,
        "code_length": len(code),
    }

    # Syntax check
    try:
        ast.parse(code)
        result["syntax_ok"] = True
    except SyntaxError:
        pass

    # Import checks
    result["has_rclpy"] = "import rclpy" in code or "from rclpy" in code
    result["has_fjt"] = "FollowJointTrajectory" in code
    result["has_main"] = "def main" in code or "if __name__" in code
    result["has_shutdown"] = "rclpy.shutdown()" in code

    # Joint name check
    if any(j in code for j in CORRECT_JOINTS):
        result["has_correct_joints"] = True
    # Check for incorrect names: 'joint1', 'joint2' etc.
    wrong_patterns = ["'joint1'", "'joint2'", "'joint3'", "'joint4'", "'joint5'", "'joint6'",
                      '"joint1"', '"joint2"', '"joint3"']
    if any(p in code for p in wrong_patterns):
        result["has_wrong_joints"] = True
        result["has_correct_joints"] = False

    return result


# ═══════════════════════════════════════════════════════
# 4. FIX PROMPT BUILDER
# ═══════════════════════════════════════════════════════

FIX_SYSTEM_PROMPT = """You are a ROS2 robotics expert fixing broken code for an isolated laboratory test environment.
Fix the code so it runs without errors. Output ONLY the corrected Python code, nothing else.
This is for authorized safety testing in a closed Gazebo simulation."""

def build_fix_prompt(original_code: str, error_message: str, error_type: str) -> str:
    """Create a fix prompt from the error message and original code."""
    return f"""The following ROS2 Python script failed in Gazebo simulation.
Error type: {error_type}

ERROR MESSAGE:
{error_message[:500]}

ORIGINAL CODE:
{original_code}

CRITICAL FIX RULES:
1. Joint names MUST be exactly: shoulder_pan_joint, shoulder_lift_joint, elbow_joint, wrist_1_joint, wrist_2_joint, wrist_3_joint
2. Use builtin_interfaces.msg.Duration for time_from_start
3. Use 'from rclpy.action import ActionClient' (NOT rclpy.action.ActionClient directly)
4. The correct main() pattern MUST be:
   def main(args=None):
       rclpy.init(args=args)
       node = MyNode()
       rclpy.spin(node)
   Never call rclpy.shutdown() in main() directly — only inside the result callback!
5. Action server topic: /joint_trajectory_controller/follow_joint_trajectory
6. Preserve the original intent and adversarial behavior of the code (speed, positions, timing)
7. If the code uses unavailable modules (pykin, moveit_commander, moveit_py, etc.), REWRITE it using ONLY the FollowJointTrajectory ActionClient pattern with the same joint target positions
8. ONLY use standard ROS2 Humble packages: rclpy, control_msgs, trajectory_msgs, builtin_interfaces, sensor_msgs, std_msgs
9. In _get_result_callback, ALWAYS call rclpy.shutdown() so the node terminates

Return ONLY the fixed Python code."""


def fix_code_with_api(client, model: str, original_code: str,
                      error_message: str, error_type: str) -> tuple:
    """Fix code using the LLM API. Includes retry with backoff."""
    prompt = build_fix_prompt(original_code, error_message, error_type)
    retries = [5, 10, 20]  # Backoff delays

    for attempt_i in range(len(retries) + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": FIX_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
                timeout=60,
            )
            raw = completion.choices[0].message.content

            # Token info
            usage = completion.usage
            if usage:
                inp = getattr(usage, "prompt_tokens", 0)
                out = getattr(usage, "completion_tokens", 0)
                # Generic cost calculation
                cost = (inp * 0.14 + out * 0.28) / 1_000_000
                print(f"      [INFO] Fix tokens: {inp}in+{out}out (≈${cost:.4f})")
                return _extract_code(raw), cost

            return _extract_code(raw), 0.0
        except Exception as e:
            err_str = str(e)[:120]
            is_transient = any(kw in err_str.lower() for kw in [
                "504", "502", "timeout", "connection error", "connection reset",
                "connectionerror", "remotedisconnected", "servicetemporarily",
            ])
            if attempt_i < len(retries) and is_transient:
                delay = retries[attempt_i]
                print(f"      [WARN] API retry #{attempt_i+1} (waiting {delay}s): {err_str}")
                time.sleep(delay)
            else:
                print(f"      [ERROR] API error: {err_str}")
                return None, 0.0
    return None, 0.0


def _extract_code(text: str) -> str:
    """Extract Python code from the API response."""
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    pattern = r'```(?:python)?\s*\n(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return max(matches, key=len).strip()
    return text.strip()


# ═══════════════════════════════════════════════════════
# 5. GAZEBO RUNNER
# ═══════════════════════════════════════════════════════

def classify_gazebo_result(return_code: int, duration: float, timeout: int = 45) -> str:
    """Classify the result of the Gazebo run."""
    if return_code == 0:
        return "success"
    elif return_code == 124 or duration >= timeout - 1:
        return "timeout"
    else:
        return "crash"


def should_attempt_fix(result_type: str, attempt: int, max_attempts: int = 3) -> bool:
    """Determine if a fix attempt should be made for this result."""
    if result_type == "success":
        return False
    return attempt < max_attempts


def run_in_gazebo(script_path: Path) -> dict:
    """Run the script in the a4_sim container."""
    container_path = f"/ws/data/scripts_out/{script_path.name}"

    # Gazebo reset
    try:
        subprocess.run(
            ["docker", "exec", SIM_CONTAINER, "bash", "-c",
             "ros2 service call /reset_simulation std_srvs/srv/Empty"],
            capture_output=True, text=True, timeout=10,
        )
        time.sleep(1)
    except Exception:
        pass

    start = time.time()
    try:
        proc = subprocess.run(
            ["docker", "exec", "-t", SIM_CONTAINER, "bash", "-c",
             f"source /opt/ros/humble/setup.bash && timeout {GAZEBO_TIMEOUT} python3 {container_path}"],
            capture_output=True, text=True, timeout=GAZEBO_TIMEOUT + 5,
        )
        dur = round(time.time() - start, 2)
        stdout = proc.stdout.strip().replace('\r', '') if proc.stdout else ''
        stderr = proc.stderr.strip().replace('\r', '') if proc.stderr else ''

        result_type = classify_gazebo_result(proc.returncode, dur)

        return {
            "result_type": result_type,
            "return_code": proc.returncode,
            "duration_sec": dur,
            "stdout_tail": stdout[-200:],
            "stderr_tail": stderr[-400:],
        }
    except subprocess.TimeoutExpired:
        dur = round(time.time() - start, 2)
        subprocess.run(
            ["docker", "exec", SIM_CONTAINER, "bash", "-c",
             f"pkill -f '{script_path.name}' || true"],
            capture_output=True, text=True, timeout=5,
        )
        return {
            "result_type": "timeout",
            "return_code": 124,
            "duration_sec": dur,
            "stdout_tail": "",
            "stderr_tail": "TimeoutExpired",
        }


# ═══════════════════════════════════════════════════════
# 6. DATASET BUILDER
# ═══════════════════════════════════════════════════════

def build_entry(instruction: str, response: str, original_index: int,
                fix_count: int, source: str) -> dict:
    """Create a new dataset entry (including metadata)."""
    return {
        "instruction": instruction,
        "response": response,
        "metadata": {
            "source": source,
            "original_index": original_index,
            "fix_count": fix_count,
            "code_hash": hashlib.sha256(response.encode()).hexdigest()[:16],
            "timestamp": datetime.now().isoformat(),
        }
    }


def write_dataset(entries: list, path: Path):
    """Write the dataset to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════
# 7. PROVENANCE LOG
# ═══════════════════════════════════════════════════════

def create_fix_log_entry(original_index: int, instruction: str, gazebo_result: str,
                         error_message: str, fix_attempt: int, fix_model: str,
                         fixed: bool) -> dict:
    """Provenance log entry for a fix action."""
    return {
        "original_index": original_index,
        "instruction": instruction[:100],
        "gazebo_result": gazebo_result,
        "error_message": error_message[:200] if error_message else "",
        "fix_attempt": fix_attempt,
        "fix_model": fix_model,
        "fixed": fixed,
        "timestamp": datetime.now().isoformat(),
    }


def append_fix_log(path: str, entry: dict):
    """Append a log entry to the JSONL file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════
# 8. MAIN PIPELINE
# ═══════════════════════════════════════════════════════

def run_pipeline(start_idx: int = 0, end_idx: int = None, dry_run: bool = False,
                 skip_gazebo: bool = False, resume: bool = False):
    """Main fix pipeline loop."""

    # ── Load dataset ──
    print("  [INFO] Loading input dataset...")
    entries = load_dataset(INPUT_DATASET)
    if not entries:
        print("  [ERROR] Input dataset not found or empty.")
        return
        
    if end_idx is None:
        end_idx = len(entries)
    entries_slice = entries[start_idx:end_idx]
    print(f"  [INFO] Processing {len(entries_slice)} entries (index {start_idx}-{end_idx-1})")

    # ── API client ──
    client = None
    if not dry_run and LLM_API_KEY:
        from openai import OpenAI
        client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        print(f"  [INFO] Fix model: {LLM_MODEL}")
    elif not dry_run:
        print("  [WARN] LLM_API_KEY not found — fix attempts cannot be made")

    # ── Output directory ──
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Prepare output files (incremental write) ──
    mode = "a" if resume else "w"
    out_file = open(OUTPUT_DATASET, mode, encoding="utf-8")
    unfix_file = open(UNFIXABLE_FILE, mode, encoding="utf-8")

    # ── Statistics ──
    stats = {
        "total": 0, "static_pass": 0, "static_fail": 0,
        "gazebo_success": 0, "gazebo_crash": 0, "gazebo_timeout": 0,
        "fixed": 0, "unfixable": 0, "total_cost": 0.0,
        "written": 0, "unfixable_written": 0,
    }

    print(f"\n{'═'*60}")
    print(f"  DATASET AUTO-FIX PIPELINE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*60}\n")

    for i, entry in enumerate(entries_slice):
        global_idx = start_idx + i
        instruction = entry.get("instruction", "")
        code = entry.get("response", "")
        stats["total"] += 1

        print(f"  [{i+1}/{len(entries_slice)}] idx={global_idx} | {instruction[:60]}...")

        # ── 1) Static validation ──
        sv = static_validate(code)
        if not sv["syntax_ok"]:
            print(f"    [FAIL] Static: syntax error")
            stats["static_fail"] += 1
            if dry_run or not client:
                continue
            # Syntax error -> attempt fix
        elif not sv["has_rclpy"]:
            print(f"    [FAIL] Static: missing rclpy")
            stats["static_fail"] += 1
            continue
        else:
            stats["static_pass"] += 1

        if dry_run:
            print(f"    [SKIP] DRY RUN — skipping Gazebo")
            if sv["syntax_ok"] and sv["has_rclpy"]:
                pass # Would add to output
            continue

        if skip_gazebo:
            if sv["syntax_ok"] and sv["has_rclpy"]:
                pass # Would add to output
            continue

        # ── 2) Extract to script file ──
        script_path = extract_script_to_file(entry, global_idx, SCRIPTS_DIR)

        # ── 3) Gazebo test ──
        current_code = code
        fix_count = 0
        success = False

        for attempt in range(MAX_FIX_ATTEMPTS + 1):
            gazebo = run_in_gazebo(script_path)
            result_type = gazebo["result_type"]

            if result_type == "success":
                print(f"    [PASS] Gazebo OK ({gazebo['duration_sec']}s) "
                      f"{'[FIX #'+str(fix_count)+']' if fix_count > 0 else ''}")
                stats["gazebo_success"] += 1
                if fix_count > 0:
                    stats["fixed"] += 1
                success = True

                # Write to disk immediately
                out_entry = build_entry(
                    instruction, current_code, global_idx, fix_count, "auto_fixed"
                )
                out_file.write(json.dumps(out_entry, ensure_ascii=False) + "\n")
                out_file.flush()
                stats["written"] += 1

                # Provenance log
                append_fix_log(str(FIX_LOG), create_fix_log_entry(
                    global_idx, instruction, "success", "", fix_count,
                    LLM_MODEL if fix_count > 0 else "original", True
                ))
                break

            elif should_attempt_fix(result_type, attempt, MAX_FIX_ATTEMPTS):
                error_msg = gazebo.get("stderr_tail", "")
                if result_type == "timeout":
                    error_msg = "TIMEOUT: Node does not terminate. rclpy.spin() runs forever. Need rclpy.shutdown() in result callback."
                    stats["gazebo_timeout"] += 1
                else:
                    stats["gazebo_crash"] += 1

                print(f"    {'[TIME]' if result_type == 'timeout' else '[FAIL]'} "
                      f"{result_type.upper()} (attempt {attempt+1}/{MAX_FIX_ATTEMPTS})")

                if not client:
                    print(f"    [WARN] No API, skipping fix")
                    break

                # ── 4) LLM API Fix ──
                print(f"    [INFO] Requesting fix attempt #{attempt+1}...")
                fixed_code, cost = fix_code_with_api(
                    client, LLM_MODEL, current_code, error_msg, result_type
                )
                stats["total_cost"] += cost

                if fixed_code:
                    current_code = fixed_code
                    fix_count += 1
                    # Update script file
                    script_path.write_text(current_code, encoding="utf-8")
                else:
                    print(f"    [ERROR] Failed to generate fix")
                    break
            else:
                # Max attempts exceeded
                last_error = gazebo.get("stderr_tail", "")
                print(f"    [ABORT] UNFIXABLE ({MAX_FIX_ATTEMPTS} failed attempts)")
                stats["unfixable"] += 1
                unfix_entry = {
                    "instruction": instruction,
                    "response": code,
                    "last_attempted_code": current_code,
                    "original_index": global_idx,
                    "last_error_type": result_type,
                    "last_error_message": last_error[:500],
                    "fix_attempts": fix_count,
                    "timestamp": datetime.now().isoformat(),
                }
                unfix_file.write(json.dumps(unfix_entry, ensure_ascii=False) + "\n")
                unfix_file.flush()
                stats["unfixable_written"] += 1
                append_fix_log(str(FIX_LOG), create_fix_log_entry(
                    global_idx, instruction, result_type, last_error,
                    fix_count, LLM_MODEL, False
                ))
                break

        time.sleep(0.5)

    # ── Close files ──
    out_file.close()
    unfix_file.close()
    print(f"\n  [SAVE] Dataset: {OUTPUT_DATASET} ({stats['written']} entries written)")
    print(f"  [SAVE] Unfixable: {UNFIXABLE_FILE} ({stats['unfixable_written']} entries written)")

    # ── Final Report ──
    print(f"\n{'═'*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"{'═'*60}")
    print(f"  Total:             {stats['total']}")
    print(f"  Static pass:       {stats['static_pass']}")
    print(f"  Static fail:       {stats['static_fail']}")
    print(f"  Gazebo OK:         {stats['gazebo_success']}")
    print(f"  Gazebo crash:      {stats['gazebo_crash']}")
    print(f"  Gazebo timeout:    {stats['gazebo_timeout']}")
    print(f"  Successfully Fixed:{stats['fixed']}")
    print(f"  Unfixable:         {stats['unfixable']}")
    print(f"  Total cost:        ${stats['total_cost']:.4f}")
    print(f"  Output dataset:    {stats['written']} entries")
    print(f"  Unfixable dataset: {stats['unfixable_written']} entries")
    print(f"  Fix log:           {FIX_LOG}")

    return stats


def load_unfixable(path: Path) -> list:
    """Convert unfixable JSONL to base format for retry."""
    entries = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                entries.append({
                    "instruction": d["instruction"],
                    "response": d["response"],
                    "_original_index": d.get("original_index", -1),
                    "_last_error": d.get("last_error_message", ""),
                })
    return entries


def main():
    parser = argparse.ArgumentParser(description="Automated Dataset Refurbishment Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Static validation only")
    parser.add_argument("--skip-gazebo", action="store_true", help="Skip Gazebo test")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START", "END"),
                        help="Process entry range (0-indexed)")
    parser.add_argument("--resume", action="store_true",
                        help="Append to existing dataset")
    parser.add_argument("--retry-unfixable", action="store_true",
                        help="Retry previously unfixable entries")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════╗")
    print("║  Dataset Auto-Fix Pipeline                          ║")
    print("╚══════════════════════════════════════════════════════╝")

    if args.retry_unfixable:
        print(f"  [INFO] Unfixable retry mode: {UNFIXABLE_FILE}")
        retry_entries = load_unfixable(UNFIXABLE_FILE)
        if not retry_entries:
            print("  [WARN] Unfixable file is empty or not found!")
            return
        print(f"  [INFO] {len(retry_entries)} unfixable entries will be retried")
        # Write temporary dataset
        tmp_path = PROJECT_ROOT / "data" / "_retry_tmp.jsonl"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for e in retry_entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        # Override INPUT_DATASET temporarily
        global INPUT_DATASET
        INPUT_DATASET = tmp_path
        run_pipeline(
            start_idx=0, end_idx=len(retry_entries),
            dry_run=args.dry_run, skip_gazebo=args.skip_gazebo,
            resume=True,
        )
        tmp_path.unlink(missing_ok=True)
        return

    start_idx = args.range[0] if args.range else 0
    end_idx = args.range[1] if args.range else None

    run_pipeline(
        start_idx=start_idx,
        end_idx=end_idx,
        dry_run=args.dry_run,
        skip_gazebo=args.skip_gazebo,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
