#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
source /ur5e_ws/install/setup.bash 2>/dev/null || true
source /ws/install/setup.bash 2>/dev/null || true

# ── Demo Mode: auto-launch Gazebo + UR5e ──
if [ "$A4_MODE" = "demo" ]; then
    echo "[A4] Demo mode — launching Gazebo + UR5e simulation..."
    # Launch UR5e Gazebo simulation in background
    ros2 launch ur_simulation_gazebo ur_sim_moveit.launch.py ur_type:=ur5e &
    GAZEBO_PID=$!

    echo "[A4] Gazebo PID: $GAZEBO_PID"
    echo "[A4] Waiting for Gazebo to initialize (15s)..."
    sleep 15
    echo "[A4] Gazebo ready. Container standing by for commands."

    # Keep container alive
    wait $GAZEBO_PID
else
    exec "$@"
fi
