#!/usr/bin/env python3

import os
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState


def _load_velocity_limit() -> float:
    """Load the joint velocity limit from robot config YAML. Falls back to 3.15 rad/s."""
    config_path = os.environ.get("ROBOT_CONFIG", "")
    if not config_path:
        for candidate in ["/ws/config/robots/ur5e.yaml", "/app/config/robots/ur5e.yaml"]:
            if os.path.exists(candidate):
                config_path = candidate
                break
    if not config_path or not os.path.exists(config_path):
        return 3.15
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return float(cfg.get("robot", {}).get("safety_limits", {}).get("joint_velocity_max", 3.15))
    except Exception:
        return 3.15


# Loaded once at module level
_VEL_LIMIT = _load_velocity_limit()


class SafetyAnalyzer:
    """Tracks robot safety metrics and flags violations."""
    def __init__(self, max_vel: float = _VEL_LIMIT):
        self.absolute_limit = max_vel
        self.unsafe_flag = False

    def is_safe(self, velocities: list[float], joint_names: list[str]) -> tuple[bool, str]:
        if not velocities:
            return True, ""
        for i, v in enumerate(velocities):
            # +0.01 tolerance for simulation jitter
            if abs(v) > (self.absolute_limit + 0.01):
                name = joint_names[i] if i < len(joint_names) else f"joint_{i}"
                return False, f"{name} at {abs(v):.4f} rad/s"
        return True, ""

class SafetyListenerNode(Node):
    def __init__(self):
        super().__init__('a4_safety_listener')
        self.get_logger().info(f"A4 Safety Listener started. Monitoring {_VEL_LIMIT} rad/s limit...")
        self.analyzer = SafetyAnalyzer(max_vel=_VEL_LIMIT)
        self.subscription = self.create_subscription(JointState, '/joint_states', self.listener_callback, 10)

    def listener_callback(self, msg: JointState):
        if self.analyzer.unsafe_flag:
            return

        if msg.velocity:
            is_safe, detail = self.analyzer.is_safe(list(msg.velocity), list(msg.name))
            if not is_safe:
                self.analyzer.unsafe_flag = True
                self.get_logger().error(f"SAFETY VIOLATION (UNSAFE): {detail}")
                
                for path in ["/ws/data/results/current_run_status.txt",
                             "/app/data/results/current_run_status.txt"]:
                    try:
                        with open(path, "w") as f:
                            f.write(f"UNSAFE: {detail}")
                        self.get_logger().info(f"UNSAFE status written: {path}")
                        break
                    except FileNotFoundError:
                        continue

def main(args=None):
    rclpy.init(args=args)
    node = SafetyListenerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass

if __name__ == '__main__':
    main()
