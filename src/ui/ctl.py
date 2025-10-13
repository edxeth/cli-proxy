#!/usr/bin/env python3
import os
import sys
import signal
import time
import subprocess
from pathlib import Path
from .ui_server import app
from ..utils.platform_helper import is_process_running, kill_process, create_detached_process

# UI service configuration
DEFAULT_PORT = 3300
CONFIG_DIR = Path.home() / '.clp/run'
PID_FILE = CONFIG_DIR / 'ui.pid'
LOG_FILE = CONFIG_DIR / 'ui.log'

def get_pid():
    """Return the PID of the running UI process, if any."""
    try:
        with open(PID_FILE, 'r') as f:
            return int(f.read().strip())
    except (IOError, ValueError):
        return None

def is_running():
    """Check whether the UI process is running."""
    pid = get_pid()
    return is_process_running(pid)

def stop_handler(signum, frame):
    """Handle termination signals for the UI daemon."""
    print("Received stop signal, shutting down...")
    if PID_FILE.exists():
        PID_FILE.unlink()
    sys.exit(0)

def start_daemon(port=DEFAULT_PORT):
    """Start the UI daemon process."""
    if is_running():
        print("UI service is already running")
        return None

    # Ensure the runtime directory exists
    CONFIG_DIR.mkdir(exist_ok=True)

    try:
        # Use production-grade WSGI servers depending on the platform
        if sys.platform == "win32":
            # Windows uses waitress
            cmd = [
                sys.executable, '-m', 'waitress',
                '--host=0.0.0.0',
                f'--port={port}',
                '--threads=4',
                'src.ui.ui_server:app'
            ]
        else:
            # Unix/Linux uses gunicorn
            cmd = [
                sys.executable, '-m', 'gunicorn',
                '-w', '2',
                '-b', f'0.0.0.0:{port}',
                'src.ui.ui_server:app'
            ]

        with open(LOG_FILE, 'a') as log:
            proc = create_detached_process(cmd, log)
            
            # Persist the PID for later management
            with open(PID_FILE, 'w') as f:
                f.write(str(proc.pid))

        # Give the service time to spin up
        time.sleep(1)

        if is_running():
            print(f"UI service started (port: {port})")
        else:
            print("UI service failed to start")

    except Exception as e:
        print(f"Failed to start UI service: {e}")

def stop_daemon():
    """Stop the UI daemon process."""
    pid = get_pid()
    if pid is None:
        print("UI service is not running")
        return

    try:
        if kill_process(pid):
            if PID_FILE.exists():
                PID_FILE.unlink()
            print("UI service stopped")
        else:
            print("Failed to stop UI service")
            if PID_FILE.exists():
                PID_FILE.unlink()
    except Exception as e:
        print(f"Failed to stop UI service: {e}")
        if PID_FILE.exists():
            PID_FILE.unlink()

def restart_daemon(port=DEFAULT_PORT):
    """Restart the UI daemon process."""
    stop_daemon()
    time.sleep(1)
    start_daemon(port)

# Compatibility helpers to match the clp CLI interface
def start(port=DEFAULT_PORT):
    """Alias for start_daemon."""
    return start_daemon(port)

def stop():
    """Alias for stop_daemon."""
    return stop_daemon()

def restart(port=DEFAULT_PORT):
    """Alias for restart_daemon."""
    return restart_daemon(port)
    print("Restarting UI service...")
    stop_daemon()
    time.sleep(1)  # Wait for the process to shut down completely
    start_daemon(port)
