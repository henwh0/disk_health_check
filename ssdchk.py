"""Get NVME devices and collect smart-log data from remote hosts"""


import re, subprocess, sys

class COLORS:
    RED: str = "\033[1;31m"
    YELLOW: str = "\033[0;33m"
    NC: str = "\033[0m"


SUSH_REASON = "MSE workflow"
TIMEOUT = 60
POWER_ON_HOUR_WARN = 43800
LINE_RE = re.compile(r"^(?P<key>[^:]+?)\s*:\s*(?P<value>.+?)\s*$")
# 'nvme list' starts with the node path, eg: /dev/nvme0n1 then the model,sn,etc...
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


def filter_smart_fields(fields: dict) -> dict:
    return {
        key: fields[key]
        for key in SMART_FIELDS
        if key in fields
    }
        

def parse_smart_log(text: str):
    """Parse raw 'nvme smart-log' output into a dict."""
    result = {}
    
    for line in text.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        
        key = match.group("key")
        value = match.group("value")
        if key in {"power_on_hours", "media_errors"}:
            value = int(value)
            
        result[key] = value
    return result


def run_ssh(host: str, remote_cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sush2", "-r", SUSH_REASON, f"root@{host}", remote_cmd],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )


def list_devices(host: str) -> list[str]:
    """Discover NVMe namespaces on 'host' via 'nvme list' """
    proc = run_ssh(host, "nvme list")
    if proc.returncode != 0:
        return []
    return [
        m.group(1)
        for m in (NODE_RE.match(line) for line in proc.stdout.splitlines())
        if m
    ]


def get_smart_result(host, devices):
    """Run nvme smart-log on host for all NVMe devices."""
    if isinstance(devices, str):
        devices = [devices]
    try:
        if devices is None:
            devices = list_devices(host)
            if not devices:
                return {
                    "host": host,
                    "ok": False,
                    "error": "no NVMe devices found (nvme list failed or empty)",
                    "devices": {},
                }
        per_device = {}
        for device in devices:
            proc = run_ssh(host, f"nvme smart-log {device}")
            if proc.returncode != 0:
                per_device[device] = {
                    "ok": False,
                    "error": (proc.stderr or proc.stdout).strip()
                    or f"exit {proc.returncode}",
                }
                continue
            fields = filter_smart_fields(parse_smart_log(proc.stdout))
            per_device[device] = (
                {"ok": True, "fields": fields}
                if fields
                else {"ok": False, "error": "no SMART fields parsed"}
            )
    except subprocess.TimeoutExpired:
        return {
            "host": host,
            "ok": False,
            "error": f"timeout after {TIMEOUT}s",
            "devices": {},
        }
    except FileNotFoundError:
        return {
            "host": host,
            "ok": False,
            "error": "sush2 not found in PATH",
            "devices": {},
        }
    return {
        "host": host,
        "ok": any(d["ok"] for d in per_device.values()),
        "devices": per_device, 
    }


def main(argv: list[str]) -> int:
    # Optional: --device may be repeated to pin specific paths on every host.
    devices, hosts = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--device" and i + 1 < len(argv):
            devices.append(argv[i + 1])
            i += 2
        else:
            hosts.append(argv[i])
            i += 1

    if not hosts:
        print(
            f"usage: {sys.argv[0]} [--device /dev/nvmeXnY ...] HOST [HOST ...]",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for host in hosts:
        result = get_smart_result(host, devices or None)
        print(f"\n{host}")
        if not result["devices"]:
            failures += 1
            print(f"  ERROR: {result['error']}", file=sys.stderr)
            continue
        for device, outcome in result["devices"].items():
            print(f"  {device}")
            if not outcome["ok"]:
                failures += 1
                print(f"    ERROR: {outcome['error']}", file=sys.stderr)
                continue
            for key, value in outcome["fields"].items():
                threshold = SMART_THRESHOLD.get(key)
                
                if threshold and threshold(value):
                    color = COLORS.YELLOW if key == "power_on_hours" else COLORS.RED
                    print(f"    {color}{key}: {value}{COLORS.NC}")
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