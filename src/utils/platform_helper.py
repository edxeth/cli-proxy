#!/usr/bin/env python3
import os
import sys
import signal
import subprocess
import psutil

def is_process_running(pid):
    """Check whether a process is running on any supported platform."""
    if pid is None:
        return False
    
    try:
        # Use psutil for cross-platform process checks
        process = psutil.Process(pid)
        return process.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False

def kill_process(pid, force=False):
    """Terminate a process and its children across platforms."""
    if not is_process_running(pid):
        return True
    
    try:
        process = psutil.Process(pid)
        
        # Fetch all child processes
        children = process.children(recursive=True)
        
        # Terminate children first
        for child in children:
            try:
                if force:
                    child.kill()
                else:
                    child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Then terminate the parent process
        if force:
            process.kill()
        else:
            process.terminate()
        
        # Wait for processes to exit
        gone, still_alive = psutil.wait_procs(children + [process], timeout=5)
        
        # Force-kill any processes that survived the graceful phase
        for p in still_alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return True  # Process no longer exists

def create_detached_process(cmd, log_file, *, cwd=None, env=None):
    """Create a detached subprocess in a cross-platform manner."""
    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )

        return proc
    except Exception as e:
        raise RuntimeError(f"Failed to create detached process: {e}")
