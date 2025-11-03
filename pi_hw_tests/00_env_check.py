#!/usr/bin/env python3
"""Environment check for hardware validation.

Verifies:
- Python version (>=3.11)
- Required packages (pandas, pyserial, fastapi)
- Serial port permissions
- Discovers /dev/ttyUSB* devices
"""

import argparse
import glob
import grp
import os
import pwd
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pi_hw_tests.common import TestResult


def check_python_version() -> tuple[bool, str]:
    """Check Python version is >= 3.11."""
    version = sys.version_info
    if version.major == 3 and version.minor >= 11:
        return True, f"Python {version.major}.{version.minor}.{version.micro}"
    else:
        return False, f"Python {version.major}.{version.minor}.{version.micro} (need >= 3.11)"


def check_package(package_name: str) -> tuple[bool, str]:
    """Check if package is importable."""
    try:
        __import__(package_name)
        return True, f"{package_name} ✓"
    except ImportError:
        return False, f"{package_name} ✗ (not installed)"


def discover_serial_ports() -> list[str]:
    """Discover /dev/ttyUSB* and /dev/ttyACM* devices."""
    ports = []
    ports.extend(glob.glob("/dev/ttyUSB*"))
    ports.extend(glob.glob("/dev/ttyACM*"))
    return sorted(ports)


def check_serial_permissions(port: str) -> tuple[bool, str]:
    """Check if current user has read/write access to serial port.

    Args:
        port: Serial port path

    Returns:
        (has_access, message) tuple
    """
    if not os.path.exists(port):
        return False, f"{port} does not exist"

    # Check read/write access
    if os.access(port, os.R_OK | os.W_OK):
        return True, f"{port} is accessible"

    # Get port owner and group
    stat_info = os.stat(port)
    owner_name = pwd.getpwuid(stat_info.st_uid).pw_name
    group_name = grp.getgrgid(stat_info.st_gid).gr_name

    # Get current user groups
    username = pwd.getpwuid(os.getuid()).pw_name
    user_groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]
    user_groups.append(pwd.getpwuid(os.getuid()).pw_name)  # Add primary group

    msg = f"{port} not accessible. Owner: {owner_name}, Group: {group_name}"

    if group_name not in user_groups:
        msg += f"\n  → Add user to '{group_name}' group: sudo usermod -a -G {group_name} {username}"
        msg += f"\n  → Then logout and login again"

    return False, msg


def main():
    parser = argparse.ArgumentParser(description="Environment check for hardware validation")
    args = parser.parse_args()

    print("="*60)
    print("Q-Sensor Hardware Validation Environment Check")
    print("="*60)

    checks_passed = True
    metrics = {}

    # Check Python version
    print("\n[1] Python Version:")
    ok, msg = check_python_version()
    print(f"  {msg}")
    if not ok:
        checks_passed = False
    metrics["python_version"] = msg

    # Check required packages
    print("\n[2] Required Packages:")
    packages = ["pandas", "pyserial", "fastapi", "q_sensor_lib", "data_store"]
    for pkg in packages:
        ok, msg = check_package(pkg)
        print(f"  {msg}")
        if not ok:
            checks_passed = False

    # Discover serial ports
    print("\n[3] Serial Port Discovery:")
    ports = discover_serial_ports()
    if ports:
        print(f"  Found {len(ports)} port(s):")
        for port in ports:
            print(f"    {port}")
        metrics["serial_ports"] = ", ".join(ports)
    else:
        print("  No /dev/ttyUSB* or /dev/ttyACM* devices found")
        print("  → Is sensor connected?")
        print("  → Check 'lsusb' or 'dmesg | tail' for USB device detection")
        metrics["serial_ports"] = "none"

    # Check permissions on discovered ports
    print("\n[4] Serial Port Permissions:")
    if ports:
        for port in ports:
            ok, msg = check_serial_permissions(port)
            print(f"  {msg}")
            if not ok:
                checks_passed = False
    else:
        print("  (no ports to check)")

    # Summary
    result = TestResult(
        passed=checks_passed,
        message="Environment check complete" if checks_passed else "Environment issues detected",
        metrics=metrics
    )
    result.print_result()

    sys.exit(0 if checks_passed else 1)


if __name__ == "__main__":
    main()
