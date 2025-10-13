#!/usr/bin/env python3
import argparse
import time
from src.codex import ctl as codex
from src.claude import ctl as claude
from src.ui import ctl as ui

def print_status():
    """Display the runtime status of all services"""
    print("=== Local Proxy Service Status ===\n")

    # Claude service status
    print("Claude proxy:")
    claude_running = claude.is_running()
    claude_pid = claude.get_pid() if claude_running else None
    claude_config = claude.claude_config_manager.active_config
    
    status_text = "Running" if claude_running else "Stopped"
    pid_text = f" (PID: {claude_pid})" if claude_pid else ""
    config_text = f" - Active config: {claude_config}" if claude_config else " - No active config"

    print(f"  Port: 3210")
    print(f"  Status: {status_text}{pid_text}")
    print(f"  Config: {config_text}")
    print()

    # Codex service status
    print("Codex proxy:")
    codex_running = codex.is_running()
    codex_pid = codex.get_pid() if codex_running else None
    codex_config = codex.codex_config_manager.active_config
    
    status_text = "Running" if codex_running else "Stopped"
    pid_text = f" (PID: {codex_pid})" if codex_pid else ""
    config_text = f" - Active config: {codex_config}" if codex_config else " - No active config"

    print(f"  Port: 3211")
    print(f"  Status: {status_text}{pid_text}")
    print(f"  Config: {config_text}")
    print()

    # UI service status
    print("UI service:")
    ui_running = ui.is_running()
    ui_pid = ui.get_pid() if ui_running else None
    
    status_text = "Running" if ui_running else "Stopped"
    pid_text = f" (PID: {ui_pid})" if ui_pid else ""

    print(f"  Port: 3300")
    print(f"  Status: {status_text}{pid_text}")

def main():
    """Main entry point that processes CLI arguments"""
    parser = argparse.ArgumentParser(
        description='CLI Proxy - local AI proxy control tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  clp start                     Start all services
  clp stop                      Stop all services
  clp status                    Display status for all services
  clp list claude               List all Claude configs
  clp active claude prod        Activate the Claude prod config""",
        prog='clp'
    )
    subparsers = parser.add_subparsers(
        dest='command', 
        title='Commands',
        description='Use clp <command> --help for detailed help',
        help='Command description'
    )

    # start command
    start = subparsers.add_parser(
        'start', 
        help='Start all proxy services',
        description='Start the codex, claude, and ui services',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  clp start                     Start all services (codex:3211, claude:3210, ui:3300)"""
    )

    # stop command
    stop = subparsers.add_parser(
        'stop', 
        help='Stop all proxy services',
        description='Stop the codex, claude, and ui services'
    )

    # restart command
    restart = subparsers.add_parser(
        'restart', 
        help='Restart all proxy services',
        description='Restart the codex, claude, and ui services',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  clp restart                   Restart all services"""
    )

    # active command
    active_parser = subparsers.add_parser(
        'active', 
        help='Activate the specified config',
        description='Set the config file to use',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  clp active claude prod        Activate the Claude prod config
  clp active codex dev          Activate the Codex dev config"""
    )
    active_parser.add_argument('service', choices=['codex', 'claude'], 
                              help='Service type', metavar='{codex,claude}')
    active_parser.add_argument('config_name', help='Name of the config to activate')

    # list command
    lists = subparsers.add_parser(
        'list', 
        help='List all configs',
        description='Show every available config for the selected service'
    )
    lists.add_argument('service', choices=['codex', 'claude'], 
                      help='Service type', metavar='{codex,claude}')

    # status command
    status_parser = subparsers.add_parser(
        'status', 
        help='Show service status',
        description='Display runtime state, PID, and active config for each proxy service'
    )

    # ui command
    ui_parser = subparsers.add_parser(
        'ui', 
        help='Launch the Web UI',
        description='Start the Web UI to visualize proxy status',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Example:
  clp ui                        Launch the UI (default port 3300)"""
    )

    # Parse arguments
    args = parser.parse_args()

    if args.command == 'start':
        print("Starting all services...")
        claude.start()
        codex.start()
        ui.start()

        # Wait for the services to start
        time.sleep(1)
        print("Startup complete!")
        print_status()
    elif args.command == 'stop':
        claude.stop()
        codex.stop()
        ui.stop()
    elif args.command == 'restart':
        claude.restart()
        codex.restart()
        ui.restart()
    elif args.command == 'active':
        if args.service == 'codex':
            codex.set_active_config(args.config_name)
        elif args.service == 'claude':
            claude.set_active_config(args.config_name)
    elif args.command == 'list':
        if args.service == 'codex':
            codex.list_configs()
        elif args.service == 'claude':
            claude.list_configs()
    elif args.command == 'status':
        print_status()
    elif args.command == 'ui':
        import webbrowser
        webbrowser.open("http://localhost:3300")
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
