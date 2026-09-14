"""Get NVME devices and collect smart-log data from remote hosts"""

from typing import Dict, Tuple, Optional, List
import re, subprocess, sys

RED: str = "\033[1;31m"
YELLOW: str = "\033[0;33m"
NC: str = "\033[0m"


SUSH_REASON = "MSE workflow"
TIMEOUT = 60
POWER_ON_HOUR_WARN = 43800
LINE_RE = re.compile(r"^(?P<key>[^:]+?)\s*:\s*(?P<value>.+?)\s*$")
NODE_RE = re.compile(r"^(/dev/nvme\d+n\d+)\b")

SMART_FIELDS = {
    "critical_warning",
    "temperature",
    "percentage_used",
    "power_cycles",
    "power_on_hours",
    "unsafe_shutdowns",
    "media_errors",
}

SMART_THRESHOLD = {
    "critical_warning": lambda value: value != "0",
    "percentage_used": lambda value: int(value.rstrip("%")) >= 100,
    "power_on_hours": lambda value: value >= POWER_ON_HOUR_WARN,
    "media_errors": lambda value: value >= 100,
}

def run_ssh(host: str, remote_cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sush2", "-r", SUSH_REASON, f"root@{host}", remote_cmd],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    
def list_devices(host: str) -> List[str]:
    """Discover NVMe namespaces on 'host' via 'nvme list' """
    proc = run_ssh(host, "nvme list")
    if proc.returncode:
        return []
    return [
        match.group(1)
        for line in proc.stdout.splitlines()
        if (match := NODE_RE.match(line))
    ] 
    
       
def parse_smart_log(text: str) -> dict:
    """Parse raw 'nvme smart-log' output into a dict."""
    fields = {}
    
    for line in text.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        
        key, value = match.groups()
        if key not in SMART_FIELDS:
            continue
        
        if key in {"power_on_hours", "media_errors"}:
            value = int(value)
            
        fields[key] = value
    
    return fields


def get_smart_result(host: str) -> Tuple[dict[str, dict], Optional[str]]:
    try:
        nvmes = list_devices(host)
        if not nvmes:
            return {}, "No NVMe devices found (nvme list failed or is empty)"
        results = {}
        
        for device in nvmes:
            try:
                proc = run_ssh(host, f"nvme smart-log {device}")
            except subprocess.TimeoutExpired:
                results[device] = {
                    "error": f"timeout after {TIMEOUT}s"
                }
                continue
            
            if proc.returncode:
                results[device] = {
                    "error": (proc.stderr or proc.stdout).strip()
                    or f"exit {proc.returncode}"
                }
                continue
            
            try:
                fields = parse_smart_log(proc.stdout)
            except (TypeError, ValueError) as exc:
                results[device] = {"error": f"invalid SMART data: {exc}"}
                continue
            
            results[device] = (
                {"fields": fields}
                if fields
                else {"error": "no SMART fields parsed"}
            )
        return results, None
                
    except subprocess.TimeoutExpired:
        return {}, f"timeout after {TIMEOUT}s"
    except FileNotFoundError:
        return {}, "sush2 not found in PATH"


def main(argv: List[str]) -> int:
    if not argv:
        print("Use hostname(s) as arg")
        return 2
    
    failures = 0
    for host in argv:
        results, error = get_smart_result(host)
        print(f"\n{host}")
        
        if error:
            failures += 1
            print(f"    ERROR: {error}", file=sys.stderr)
            continue
        
        for device, result in results.items():
            print(f"    {device}")
            
            if "error" in result:
                failures += 1
                print(f"    ERROR: {result['error']}", file=sys.stderr)
                continue
            
            for key, value in result["fields"].items():
                threshold = SMART_THRESHOLD.get(key)
                if threshold and threshold(value):
                    color = YELLOW if key == "power_on_hours" else RED
                    print(f"    {color}{key}: {value}{NC}")
                else:
                    print(f"    {key}: {value}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
    
    
    
    
""" example nvme-cli smart-log output:

server.name.123.frc2
/dev/nvme0n1
    smart_log_for_nvme_device: nvme0n1 namespace-id:ffffffff
    critical_warning: 0
    temperature: 107 °F (315 K)
    available_spare: 100%
    available_spare_threshold: 10%
    percentage_used: 38%
    endurance_group_critical_warning_summary: 0
    data_units_read: 1172184019 (600.16 TB)
    data_units_written: 289658091 (148.30 TB)
    host_read_commands: 10985086910
    host_write_commands: 5102646279
    controller_busy_time: 15051
    power_cycles: 171
    power_on_hours: 27263
    unsafe_shutdowns: 164
    media_errors: 0
    num_err_log_entries: 0
    warning_temperature_time: 0
    critical_composite_temperature_time: 0
    temperature_sensor_1: 107 °F (315 K)
    thermal_management_t1_trans_count: 0
    thermal_management_t2_trans_count: 0
    thermal_management_t1_total_time: 0
    thermal_management_t2_total_time: 0
"""
