#!/usr/bin/env python3
"""
Automated Dataset Recovery Pipeline (Reflection Loop)
=============================================================
FEATURES:
- INFINITE NETWORK PROTECTION: Retries indefinitely on network failures.
- CRASH PROTECTION: Hardened against empty logs and API crashes.
- NO MOCK CODE: Forces 100% physical ROS 2 code generation.
- AUTOMATIC RESUME: Resumes gracefully from the last processed index.
"""
import argparse
import json
import os
import re
import subprocess
import time
import sys
from pathlib import Path

from dotenv import load_dotenv
import openai
from openai import OpenAI

load_dotenv()

# ── Paths ──
PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNFIXABLE_FILE = PROJECT_ROOT / "data" / "unfixable_dataset.jsonl"
RECOVERED_FILE = PROJECT_ROOT / "data" / "recovered_dataset.jsonl"
FINAL_UNFIXABLE_FILE = PROJECT_ROOT / "data" / "final_failed_dataset.jsonl" 
RECOVERY_DIR = PROJECT_ROOT / "data" / "scripts_recovery"

SIM_CONTAINER = "a4_sim"
GAZEBO_TIMEOUT = 45
MAX_ATTEMPTS = 3 

# ── LLM API (INITIALIZATION) ──
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "generic-reasoning-model")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")

# Global client
client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=90.0)

RECOVERY_SYSTEM_PROMPT = """You are an Elite ROS2 Robotics Engineer. Our goal is high-fidelity data generation for a UR5e robot in Gazebo.

CRITICAL RULES:
1. NO MOCKING: This dataset is for training real robotics LLMs. Do NOT use `pass`, fake classes, or mock logic. Code MUST be production-quality ROS2.
2. HALUCINATION CLEANUP: Ignore YOLO or cameras if mentioned. Translate intent into physical moves using `FollowJointTrajectory`.
3. NATIVE LIBRARIES ONLY: Use only `rclpy`, `control_msgs`, `trajectory_msgs`, `sensor_msgs`.
4. ACTION SERVER NAMES: Use `/joint_trajectory_controller/follow_joint_trajectory` for UR5e.
5. GRACEFUL SHUTDOWN: Call `rclpy.shutdown()` inside callbacks when task is done.
6. Output ONLY standard Python code in ```python blocks."""

def extract_python_code(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    pattern = r'```python\n(.*?)\n```'
    match = re.search(pattern, text, re.DOTALL)
    if match: return match.group(1).strip()
    pattern_alt = r'```(.*?)\n```'
    match_alt = re.search(pattern_alt, text, re.DOTALL)
    return match_alt.group(1).strip() if match_alt else text.strip()

def get_processed_indices():
    indices = set()
    for f in [RECOVERED_FILE, FINAL_UNFIXABLE_FILE]:
        if f.exists():
            with open(f, 'r', encoding='utf-8') as jfile:
                for line in jfile:
                    try:
                        data = json.loads(line)
                        idx = data.get("index") if data.get("index") is not None else data.get("original_index")
                        if idx is not None: indices.add(idx)
                    except: continue
    return indices

def safe_append_jsonl(file_path, data):
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())

def tdd_check(code, index):
    """Pre-Gazebo Syntax Check"""
    temp_test = RECOVERY_DIR / f"tdd_{index:04d}.py"
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    temp_test.write_text(code, encoding='utf-8')
    try:
        subprocess.run([sys.executable, "-m", "py_compile", str(temp_test)], check=True, capture_output=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, f"Syntax Error: {e.stderr.decode()}"
    finally:
        if temp_test.exists(): temp_test.unlink()

def run_in_gazebo(script_content, index):
    temp_file = RECOVERY_DIR / f"test_{index:04d}.py"
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    temp_file.write_text(script_content, encoding='utf-8')
    
    try:
        inspect_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", SIM_CONTAINER]
        is_running = subprocess.check_output(inspect_cmd, text=True).strip() == "true"
        if not is_running:
            print(f"  [WARN] Container {SIM_CONTAINER} restarting...")
            subprocess.run(["docker", "start", SIM_CONTAINER], check=True)
            time.sleep(10)
    except: return False, 0, "docker_not_found", "Docker error"

    try:
        subprocess.run(["docker", "cp", str(temp_file), f"{SIM_CONTAINER}:/tmp/test.py"], check=True, capture_output=True)
    except Exception as e: return False, 0, "docker_error", str(e)

    cmd = ["docker", "exec", SIM_CONTAINER, "bash", "-c", f"source /opt/ros/humble/setup.bash && timeout {GAZEBO_TIMEOUT} python3 /tmp/test.py"]
    
    start_time = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=GAZEBO_TIMEOUT + 15)
        duration = time.time() - start_time
        log = f"STDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"
        
        if proc.returncode == 0:
            return True, duration, "success", log
        elif proc.returncode == 124:
            if any(re.search(p, log, re.IGNORECASE) for p in [r"Goal\s+reached", r"Success"]):
                return True, duration, "success_with_timeout", log
            return False, duration, "timeout", log
        else:
            return False, duration, "crash", log
    except subprocess.TimeoutExpired:
        return False, GAZEBO_TIMEOUT, "hard_timeout", "Subprocess hung"

def process_entry(entry, current_count, total_count):
    idx = entry.get('index') if entry.get('index') is not None else entry.get('original_index')
    if idx is None: return False
    
    instruction = entry['instruction']
    print(f"\n[{ (current_count/total_count)*100:.1f}%] [Entry {idx}] Processing: {instruction[:70]}...")
    
    history = []
    attempt = 1
    while attempt <= MAX_ATTEMPTS:
        print(f"  > Attempt {attempt}/{MAX_ATTEMPTS}...")
        
        # Protection against IndexError on attempt 1
        if attempt == 1 or not history:
            user_msg = f"Instruction: {instruction}"
        else:
            last_log = history[-1].get('log', "Unknown error")
            user_msg = f"Previous failed. Fix it using logs (NO MOCK):\nInstruction: {instruction}\n\nFAILURE LOG:\n{last_log}"

        # Infinite API/Network loop
        python_code = None
        while python_code is None:
            try:
                response = client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[{"role": "system", "content": RECOVERY_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
                    temperature=0.2
                )
                python_code = extract_python_code(response.choices[0].message.content)
            except (openai.APIConnectionError, openai.APITimeoutError):
                print("  [NETWORK ERROR] Waiting 15s to retry API...")
                time.sleep(15)
            except Exception as e:
                print(f"  [API ERROR] {e}. Retrying in 10s...")
                time.sleep(10)

        # TDD Pre-Check
        valid, msg = tdd_check(python_code, idx)
        if not valid:
            print(f"  [FAIL] Syntax Check Failed. Requesting fix...")
            history.append({"attempt": attempt, "log": msg, "duration": 0})
            attempt += 1
            continue

        # Gazebo Execution Test
        success, duration, error_type, full_log = run_in_gazebo(python_code, idx)
        history.append({"attempt": attempt, "code": python_code, "success": success, "log": full_log, "duration": duration})

        if success:
            print(f"  [SUCCESS] Recovered in {duration:.2f}s")
            safe_append_jsonl(RECOVERED_FILE, {
                "index": idx, "instruction": instruction, "response": python_code,
                "metadata": {"attempts": attempt, "total_duration": sum(h['duration'] for h in history)}
            })
            return True
        
        print(f"  [FAIL] Type: {error_type}")
        attempt += 1

    print(f"  [ABORT] Exhausted max attempts.")
    safe_append_jsonl(FINAL_UNFIXABLE_FILE, {"index": idx, "instruction": instruction, "history": history})
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not LLM_API_KEY:
        print("ERROR: LLM_API_KEY environment variable not set. Please set it in .env file.")
        sys.exit(1)

    processed = get_processed_indices()
    print(f"Already processed: {len(processed)}")
    
    unfixables = []
    if UNFIXABLE_FILE.exists():
        with open(UNFIXABLE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    idx = data.get('index') or data.get('original_index')
                    if idx is not None and idx not in processed: unfixables.append(data)
                except: continue

    if args.limit: unfixables = unfixables[:args.limit]
    total = len(unfixables)
    if total == 0:
        print("No entries to recover.")
        return

    print(f"Starting recovery for {total} entries...")
    for i, entry in enumerate(unfixables, 1):
        process_entry(entry, i, total)

if __name__ == "__main__":
    main()
