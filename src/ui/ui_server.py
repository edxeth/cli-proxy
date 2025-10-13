import json
import webbrowser
import time
import json
from pathlib import Path
from typing import Any, Dict
from flask import Flask, jsonify, send_file, request
import requests
import uuid
from src.codex.proxy import INSTRUCTIONS_CLI
import os

from src.utils.usage_parser import (
    METRIC_KEYS,
    empty_metrics,
    format_usage_value,
    merge_usage_metrics,
    normalize_usage_record,
)

# Data directory (absolute path)
DATA_DIR = Path.home() / '.clp/data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = Path(__file__).resolve().parent / 'static'

LOG_FILE = DATA_DIR / 'proxy_requests.jsonl'
OLD_LOG_FILE = DATA_DIR / 'traffic_statistics.jsonl'
HISTORY_FILE = DATA_DIR / 'history_usage.json'
SYSTEM_CONFIG_FILE = DATA_DIR / 'system.json'

if OLD_LOG_FILE.exists() and not LOG_FILE.exists():
    try:
        OLD_LOG_FILE.rename(LOG_FILE)
    except OSError:
        pass

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path='/static')


def _safe_json_load(line: str) -> Dict[str, Any]:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def _config_signature(config_entry: Dict[str, Any]) -> tuple:
    """Create a comparable signature for a config entry to help detect renames."""
    if not isinstance(config_entry, dict):
        return tuple()
    return (
        config_entry.get('base_url'),
        config_entry.get('auth_token'),
        config_entry.get('api_key'),
    )


def _detect_config_renames(old_configs: Dict[str, Any], new_configs: Dict[str, Any]) -> Dict[str, str]:
    """Return mapping of {old_name: new_name} for configs that only changed key names."""
    rename_map: Dict[str, str] = {}
    if not isinstance(old_configs, dict) or not isinstance(new_configs, dict):
        return rename_map

    old_signatures: Dict[tuple, list[str]] = {}
    for name, cfg in old_configs.items():
        sig = _config_signature(cfg)
        old_signatures.setdefault(sig, []).append(name)

    new_signatures: Dict[tuple, list[str]] = {}
    for name, cfg in new_configs.items():
        sig = _config_signature(cfg)
        new_signatures.setdefault(sig, []).append(name)

    for signature, old_names in old_signatures.items():
        new_names = new_signatures.get(signature)
        if not new_names:
            continue
        if set(old_names) == set(new_names):
            continue
        if len(old_names) == len(new_names) == 1:
            old_name = old_names[0]
            new_name = new_names[0]
            if old_name != new_name:
                rename_map[old_name] = new_name

    return rename_map


def _rename_history_channels(service: str, rename_map: Dict[str, str]) -> None:
    if not rename_map:
        return
    history_usage = load_history_usage()
    service_bucket = history_usage.get(service)
    if not service_bucket:
        return

    changed = False
    for old_name, new_name in rename_map.items():
        if old_name == new_name:
            continue
        if old_name not in service_bucket:
            continue

        existing_metrics = service_bucket.pop(old_name)
        target_metrics = service_bucket.get(new_name)
        if target_metrics:
            merge_usage_metrics(target_metrics, existing_metrics)
        else:
            service_bucket[new_name] = existing_metrics
        changed = True

    if changed:
        save_history_usage(history_usage)


def _rename_log_channels(service: str, rename_map: Dict[str, str]) -> None:
    if not rename_map or not LOG_FILE.exists():
        return

    temp_path = LOG_FILE.with_suffix('.tmp')
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as src, open(temp_path, 'w', encoding='utf-8') as dst:
            for raw_line in src:
                if not raw_line.strip():
                    dst.write(raw_line)
                    continue
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    dst.write(raw_line)
                    continue

                if record.get('service') == service:
                    channel_name = record.get('channel')
                    if channel_name in rename_map:
                        record['channel'] = rename_map[channel_name]
                        raw_line = json.dumps(record, ensure_ascii=False) + '\n'
                dst.write(raw_line)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    temp_path.replace(LOG_FILE)


def _sync_router_config_names(service: str, rename_map: Dict[str, str]) -> None:
    """Synchronize config names inside the model router configuration."""
    if not rename_map:
        return

    router_config_file = DATA_DIR / 'model_router_config.json'
    if not router_config_file.exists():
        return

    try:
        with open(router_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        changed = False

        # Update config names inside modelMappings
        if 'modelMappings' in config and service in config['modelMappings']:
            for mapping in config['modelMappings'][service]:
                if mapping.get('source_type') == 'config' and mapping.get('source') in rename_map:
                    old_name = mapping['source']
                    new_name = rename_map[old_name]
                    mapping['source'] = new_name
                    changed = True

        # Update config names inside configMappings
        if 'configMappings' in config and service in config['configMappings']:
            for mapping in config['configMappings'][service]:
                if mapping.get('config') in rename_map:
                    old_name = mapping['config']
                    new_name = rename_map[old_name]
                    mapping['config'] = new_name
                    changed = True

        if changed:
            with open(router_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"Failed to sync router config names: {e}")


def _sync_loadbalance_config_names(service: str, rename_map: Dict[str, str]) -> None:
    """Synchronize config names inside the load-balancer configuration."""
    if not rename_map:
        return

    lb_config_file = DATA_DIR / 'lb_config.json'
    if not lb_config_file.exists():
        return

    try:
        with open(lb_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        changed = False
        service_config = config.get('services', {}).get(service, {})

        # Update config names inside currentFailures
        current_failures = service_config.get('currentFailures', {})
        new_failures = {}
        for config_name, count in current_failures.items():
            if config_name in rename_map:
                new_failures[rename_map[config_name]] = count
                changed = True
            else:
                new_failures[config_name] = count

        if changed:
            service_config['currentFailures'] = new_failures

        # Update config names inside excludedConfigs
        excluded_configs = service_config.get('excludedConfigs', [])
        new_excluded = []
        for config_name in excluded_configs:
            if config_name in rename_map:
                new_excluded.append(rename_map[config_name])
                changed = True
            else:
                new_excluded.append(config_name)

        if changed:
            service_config['excludedConfigs'] = new_excluded
            config.setdefault('services', {})[service] = service_config

            with open(lb_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"Failed to sync load-balancer config names: {e}")


def _cleanup_deleted_configs(service: str, old_configs: Dict[str, Any], new_configs: Dict[str, Any]) -> None:
    """Remove references to deleted configs in routing and load-balancer settings."""
    if not isinstance(old_configs, dict) or not isinstance(new_configs, dict):
        return

    # Determine which configs were removed
    deleted_configs = set(old_configs.keys()) - set(new_configs.keys())
    if not deleted_configs:
        return

    # Clean references in the router configuration
    _cleanup_router_config_references(service, deleted_configs)
    # Clean references in the load-balancer configuration
    _cleanup_loadbalance_config_references(service, deleted_configs)


def _cleanup_router_config_references(service: str, deleted_configs: set) -> None:
    """Purge router-config references to deleted configs."""
    router_config_file = DATA_DIR / 'model_router_config.json'
    if not router_config_file.exists():
        return

    try:
        with open(router_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        changed = False

        # Filter modelMappings entries for deleted configs
        if 'modelMappings' in config and service in config['modelMappings']:
            original_mappings = config['modelMappings'][service][:]
            config['modelMappings'][service] = [
                mapping for mapping in original_mappings
                if not (mapping.get('source_type') == 'config' and mapping.get('source') in deleted_configs)
            ]
            if len(config['modelMappings'][service]) != len(original_mappings):
                changed = True

        # Filter configMappings entries for deleted configs
        if 'configMappings' in config and service in config['configMappings']:
            original_mappings = config['configMappings'][service][:]
            config['configMappings'][service] = [
                mapping for mapping in original_mappings
                if mapping.get('config') not in deleted_configs
            ]
            if len(config['configMappings'][service]) != len(original_mappings):
                changed = True

        if changed:
            with open(router_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"Failed to clean router config references: {e}")


def _cleanup_loadbalance_config_references(service: str, deleted_configs: set) -> None:
    """Remove load-balancer references to deleted configs."""
    lb_config_file = DATA_DIR / 'lb_config.json'
    if not lb_config_file.exists():
        return

    try:
        with open(lb_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        changed = False
        service_config = config.get('services', {}).get(service, {})

        # Filter currentFailures entries for deleted configs
        current_failures = service_config.get('currentFailures', {})
        new_failures = {
            config_name: count for config_name, count in current_failures.items()
            if config_name not in deleted_configs
        }
        if len(new_failures) != len(current_failures):
            service_config['currentFailures'] = new_failures
            changed = True

        # Filter excludedConfigs entries for deleted configs
        excluded_configs = service_config.get('excludedConfigs', [])
        new_excluded = [
            config_name for config_name in excluded_configs
            if config_name not in deleted_configs
        ]
        if len(new_excluded) != len(excluded_configs):
            service_config['excludedConfigs'] = new_excluded
            changed = True

        if changed:
            config.setdefault('services', {})[service] = service_config
            with open(lb_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"Failed to clean load-balancer config references: {e}")


def _apply_channel_renames(service: str, rename_map: Dict[str, str]) -> None:
    if not rename_map:
        return
    _rename_history_channels(service, rename_map)
    _rename_log_channels(service, rename_map)
    _sync_router_config_names(service, rename_map)
    _sync_loadbalance_config_names(service, rename_map)


def load_system_config() -> Dict[str, Any]:
    """Load the persisted system configuration."""
    if not SYSTEM_CONFIG_FILE.exists():
        default_config = {'logLimit': 50}
        save_system_config(default_config)
        return default_config

    try:
        with open(SYSTEM_CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # Ensure defaults exist
        config.setdefault('logLimit', 50)
        return config
    except (json.JSONDecodeError, OSError):
        return {'logLimit': 50}


def save_system_config(config: Dict[str, Any]) -> None:
    """Persist the system configuration to disk."""
    with open(SYSTEM_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def trim_logs_to_limit(limit: int) -> None:
    """Trim the request log file to the specified number of entries."""
    if not LOG_FILE.exists():
        return

    logs = load_logs()
    if len(logs) <= limit:
        return

    # Retain only the latest `limit` entries
    trimmed_logs = logs[-limit:]

    # Rewrite the log file with the trimmed entries
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        for log in trimmed_logs:
            f.write(json.dumps(log, ensure_ascii=False) + '\n')


def load_logs() -> list[Dict[str, Any]]:
    logs: list[Dict[str, Any]] = []
    log_path = LOG_FILE if LOG_FILE.exists() else (
        OLD_LOG_FILE if OLD_LOG_FILE.exists() else None
    )
    if log_path is None:
        return logs

    with open(log_path, 'r', encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            entry = _safe_json_load(line)
            if not entry:
                continue
            service = entry.get('service') or entry.get('usage', {}).get('service') or 'unknown'
            entry['usage'] = normalize_usage_record(service, entry.get('usage'))
            logs.append(entry)
    return logs


def load_history_usage() -> Dict[str, Dict[str, Dict[str, int]]]:
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    history: Dict[str, Dict[str, Dict[str, int]]] = {}
    for service, channels in (data or {}).items():
        if not isinstance(channels, dict):
            continue
        service_bucket: Dict[str, Dict[str, int]] = {}
        for channel, metrics in channels.items():
            normalized = empty_metrics()
            if isinstance(metrics, dict):
                merge_usage_metrics(normalized, metrics)
            service_bucket[channel] = normalized
        history[service] = service_bucket
    return history


def save_history_usage(data: Dict[str, Dict[str, Dict[str, int]]]) -> None:
    serializable = {
        service: {
            channel: {key: int(value) for key, value in metrics.items()}
            for channel, metrics in channels.items()
        }
        for service, channels in data.items()
    }
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def aggregate_usage_from_logs(logs: list[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    aggregated: Dict[str, Dict[str, Dict[str, int]]] = {}
    for entry in logs:
        usage = entry.get('usage', {})
        metrics = usage.get('metrics', {})
        if not metrics:
            continue
        service = usage.get('service') or entry.get('service') or 'unknown'
        channel = entry.get('channel') or 'unknown'
        service_bucket = aggregated.setdefault(service, {})
        channel_bucket = service_bucket.setdefault(channel, empty_metrics())
        merge_usage_metrics(channel_bucket, metrics)
    return aggregated


def merge_history_usage(base: Dict[str, Dict[str, Dict[str, int]]],
                        addition: Dict[str, Dict[str, Dict[str, int]]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    for service, channels in addition.items():
        service_bucket = base.setdefault(service, {})
        for channel, metrics in channels.items():
            channel_bucket = service_bucket.setdefault(channel, empty_metrics())
            merge_usage_metrics(channel_bucket, metrics)
    return base


def combine_usage_maps(current: Dict[str, Dict[str, Dict[str, int]]],
                       history: Dict[str, Dict[str, Dict[str, int]]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    combined: Dict[str, Dict[str, Dict[str, int]]] = {}
    services = set(current.keys()) | set(history.keys())
    for service in services:
        combined_channels: Dict[str, Dict[str, int]] = {}
        current_channels = current.get(service, {})
        history_channels = history.get(service, {})
        all_channels = set(current_channels.keys()) | set(history_channels.keys())
        for channel in all_channels:
            metrics = empty_metrics()
            if channel in current_channels:
                merge_usage_metrics(metrics, current_channels[channel])
            if channel in history_channels:
                merge_usage_metrics(metrics, history_channels[channel])
            combined_channels[channel] = metrics
        combined[service] = combined_channels
    return combined


def compute_total_metrics(channels_map: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    totals = empty_metrics()
    for metrics in channels_map.values():
        merge_usage_metrics(totals, metrics)
    return totals


def format_metrics(metrics: Dict[str, int]) -> Dict[str, str]:
    return {key: format_usage_value(metrics.get(key, 0)) for key in METRIC_KEYS}


def build_usage_snapshot() -> Dict[str, Any]:
    logs = load_logs()
    current_usage = aggregate_usage_from_logs(logs)
    history_usage = load_history_usage()
    combined_usage = combine_usage_maps(current_usage, history_usage)
    return {
        'logs': logs,
        'current_usage': current_usage,
        'history_usage': history_usage,
        'combined_usage': combined_usage
    }

@app.route('/')
def index():
    """Serve the main UI page."""
    index_file = STATIC_DIR / 'index.html'
    return send_file(index_file)

@app.route('/static/<path:filename>')
def static_files(filename):
    """Serve static assets from the UI bundle."""
    return send_file(STATIC_DIR / filename)

@app.route('/api/status')
def get_status():
    """Return the current status of all services."""
    try:
        # Query controllers directly instead of relying on status.json
        from src.claude import ctl as claude
        from src.codex import ctl as codex
        from src.config.cached_config_manager import claude_config_manager, codex_config_manager
        
        claude_running = claude.is_running()
        claude_pid = claude.get_pid() if claude_running else None
        claude_config = claude_config_manager.active_config
        
        codex_running = codex.is_running()
        codex_pid = codex.get_pid() if codex_running else None
        codex_config = codex_config_manager.active_config
        
        # Count available configurations
        claude_configs = len(claude_config_manager.configs)
        codex_configs = len(codex_config_manager.configs)
        total_configs = claude_configs + codex_configs
        
        usage_snapshot = build_usage_snapshot()
        logs = usage_snapshot['logs']
        request_count = len(logs)
        combined_usage = usage_snapshot['combined_usage']

        service_usage_totals: Dict[str, Dict[str, int]] = {}
        for service_name, channels in combined_usage.items():
            service_usage_totals[service_name] = compute_total_metrics(channels)

        for expected_service in ('claude', 'codex'):
            service_usage_totals.setdefault(expected_service, empty_metrics())

        overall_totals = empty_metrics()
        for totals in service_usage_totals.values():
            merge_usage_metrics(overall_totals, totals)

        usage_summary = {
            'totals': overall_totals,
            'formatted_totals': format_metrics(overall_totals),
            'per_service': {
                service: {
                    'metrics': totals,
                    'formatted': format_metrics(totals)
                }
                for service, totals in service_usage_totals.items()
            }
        }
        
        # Count filter rules
        filter_file = Path.home() / '.clp' / 'filter.json'
        filter_count = 0
        if filter_file.exists():
            try:
                with open(filter_file, 'r', encoding='utf-8') as f:
                    filter_data = json.load(f)
                    if isinstance(filter_data, list):
                        filter_count = len(filter_data)
                    elif isinstance(filter_data, dict):
                        filter_count = 1
            except (json.JSONDecodeError, IOError):
                filter_count = 0
        
        data = {
            'services': {
                'claude': {
                    'running': claude_running,
                    'pid': claude_pid,
                    'config': claude_config
                },
                'codex': {
                    'running': codex_running,
                    'pid': codex_pid,
                    'config': codex_config
                }
            },
            'request_count': request_count,
            'config_count': total_configs,
            'filter_count': filter_count,
            'last_updated': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'usage_summary': usage_summary
        }
        
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config/<service>', methods=['GET'])
def get_config(service):
    """Fetch the contents of the specified service configuration file."""
    try:
        if service not in ['claude', 'codex']:
            return jsonify({'error': 'Invalid service name'}), 400
        
        config_file = Path.home() / '.clp' / f'{service}.json'
        config_file.parent.mkdir(parents=True, exist_ok=True)

        if not config_file.exists():
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

        content = config_file.read_text(encoding='utf-8')
        if not content.strip():
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            content = config_file.read_text(encoding='utf-8')

        return jsonify({'content': content})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config/<service>', methods=['POST'])
def save_config(service):
    """Persist the configuration file for the specified service."""
    try:
        if service not in ['claude', 'codex']:
            return jsonify({'error': 'Invalid service name'}), 400
        
        data = request.get_json()
        content = data.get('content', '')

        if not content:
            return jsonify({'error': 'Content cannot be empty'}), 400

        # Validate JSON format
        try:
            new_configs = json.loads(content)
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 400

        config_file = Path.home() / '.clp' / f'{service}.json'
        old_content = None
        old_configs: Dict[str, Any] = {}

        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                old_content = f.read()
            try:
                old_configs = json.loads(old_content)
            except json.JSONDecodeError:
                old_configs = {}

        rename_map = _detect_config_renames(old_configs, new_configs)

        try:
            # Write the new content directly
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write(content)

            _apply_channel_renames(service, rename_map)
            _cleanup_deleted_configs(service, old_configs, new_configs)
        except Exception as exc:
            # Restore the previous config to avoid partial updates
            if old_content is not None:
                with open(config_file, 'w', encoding='utf-8') as f:
                    f.write(old_content)
            else:
                config_file.unlink(missing_ok=True)
            return jsonify({'error': f'Failed to save configuration: {exc}'}), 500

        return jsonify({'success': True, 'message': f'{service} configuration saved successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/filter', methods=['GET'])
def get_filter():
    """Retrieve the request filter configuration file."""
    try:
        filter_file = Path.home() / '.clp' / 'filter.json'
        
        if not filter_file.exists():
            # Provide a default filter template
            default_content = '[\n  {\n    "source": "example_text",\n    "target": "replacement_text",\n    "op": "replace"\n  }\n]'
            return jsonify({'content': default_content})
        
        with open(filter_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({'content': content})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/filter', methods=['POST'])
def save_filter():
    """Persist the request filter configuration."""
    try:
        data = request.get_json()
        content = data.get('content', '')
        
        if not content:
            return jsonify({'error': 'Content cannot be empty'}), 400
        
        # Validate JSON format and schema
        try:
            filter_data = json.loads(content)
            if isinstance(filter_data, list):
                for rule in filter_data:
                    if not isinstance(rule, dict):
                        return jsonify({'error': 'Each filter rule must be an object'}), 400
                    if 'source' not in rule or 'op' not in rule:
                        return jsonify({'error': 'Each rule must have "source" and "op" fields'}), 400
                    if rule['op'] not in ['replace', 'remove']:
                        return jsonify({'error': 'op must be "replace" or "remove"'}), 400
                    if rule['op'] == 'replace' and 'target' not in rule:
                        return jsonify({'error': 'replace operation requires "target" field'}), 400
            elif isinstance(filter_data, dict):
                if 'source' not in filter_data or 'op' not in filter_data:
                    return jsonify({'error': 'Rule must have "source" and "op" fields'}), 400
                if filter_data['op'] not in ['replace', 'remove']:
                    return jsonify({'error': 'op must be "replace" or "remove"'}), 400
                if filter_data['op'] == 'replace' and 'target' not in filter_data:
                    return jsonify({'error': 'replace operation requires "target" field'}), 400
            else:
                return jsonify({'error': 'Filter data must be an object or array of objects'}), 400
                
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 400
        
        filter_file = Path.home() / '.clp' / 'filter.json'
        
        # Write the new content without creating a backup
        with open(filter_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': 'Filter configuration saved successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
def get_logs():
    """Return the most recent request logs."""
    try:
        logs = load_logs()
        return jsonify(logs[-10:][::-1])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs/all')
def get_all_logs():
    """Return every recorded request log."""
    try:
        logs = load_logs()
        return jsonify(logs[::-1])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs', methods=['DELETE'])
def clear_logs():
    """Clear request logs while preserving usage history."""
    try:
        logs = load_logs()
        if logs:
            aggregated = aggregate_usage_from_logs(logs)
            if aggregated:
                history_usage = load_history_usage()
                merged = merge_history_usage(history_usage, aggregated)
                save_history_usage(merged)

        log_path = LOG_FILE if LOG_FILE.exists() else (
            OLD_LOG_FILE if OLD_LOG_FILE.exists() else LOG_FILE
        )
        log_path.write_text('', encoding='utf-8')
        if log_path != LOG_FILE:
            LOG_FILE.touch(exist_ok=True)
        
        return jsonify({'success': True, 'message': 'Logs cleared successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/usage/details')
def get_usage_details():
    """Return combined usage metrics across logs and history."""
    try:
        snapshot = build_usage_snapshot()
        combined_usage = snapshot['combined_usage']

        services_payload: Dict[str, Any] = {}
        for service, channels in combined_usage.items():
            overall_metrics = compute_total_metrics(channels)
            services_payload[service] = {
                'overall': {
                    'metrics': overall_metrics,
                    'formatted': format_metrics(overall_metrics)
                },
                'channels': {
                    channel: {
                        'metrics': metrics,
                        'formatted': format_metrics(metrics)
                    }
                    for channel, metrics in channels.items()
                }
            }

        totals_metrics = empty_metrics()
        for service_data in services_payload.values():
            merge_usage_metrics(totals_metrics, service_data['overall']['metrics'])

        response = {
            'totals': {
                'metrics': totals_metrics,
                'formatted': format_metrics(totals_metrics)
            },
            'services': services_payload
        }
        return jsonify(response)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/usage/clear', methods=['DELETE'])
def clear_usage():
    """Clear token usage records and associated logs."""
    try:
        # Step 1: clear the logs (reuse existing logic)
        logs = load_logs()
        if logs:
            aggregated = aggregate_usage_from_logs(logs)
            if aggregated:
                history_usage = load_history_usage()
                merged = merge_history_usage(history_usage, aggregated)
                save_history_usage(merged)

        log_path = LOG_FILE if LOG_FILE.exists() else (
            OLD_LOG_FILE if OLD_LOG_FILE.exists() else LOG_FILE
        )
        log_path.write_text('', encoding='utf-8')
        if log_path != LOG_FILE:
            LOG_FILE.touch(exist_ok=True)

        # Step 2: reset all values stored in history_usage.json
        save_history_usage({"claude": {}, "codex":{}})

        return jsonify({'success': True, 'message': 'Token usage records cleared successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/switch-config', methods=['POST'])
def switch_config():
    """Switch the active configuration for a service."""
    try:
        data = request.get_json()
        service = data.get('service')
        config = data.get('config')

        if not service or not config:
            return jsonify({'error': 'Missing service or config parameter'}), 400

        if service not in ['claude', 'codex']:
            return jsonify({'error': 'Invalid service name'}), 400

        # Import the appropriate config manager
        if service == 'claude':
            from src.config.cached_config_manager import claude_config_manager as config_manager
        else:
            from src.config.cached_config_manager import codex_config_manager as config_manager

        # Attempt to switch the configuration
        if config_manager.set_active_config(config):
            # Verify the configuration actually switched
            actual_config = config_manager.active_config
            if actual_config == config:
                return jsonify({
                    'success': True,
                    'message': f'{service} configuration switched to {config}',
                    'active_config': actual_config
                })
            else:
                return jsonify({
                    'success': False,
                    'message': f'Configuration verification failed; current config is {actual_config}'
                })
        else:
            return jsonify({'success': False, 'message': f'Configuration {config} does not exist'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routing/config', methods=['GET'])
def get_routing_config():
    """Retrieve the model routing configuration."""
    try:
        routing_config_file = DATA_DIR / 'model_router_config.json'
        
        # If the configuration file is missing, return the default structure
        if not routing_config_file.exists():
            default_config = {
                'mode': 'default',
                'modelMappings': {
                    'claude': [],
                    'codex': []
                },
                'configMappings': {
                    'claude': [],
                    'codex': []
                }
            }
            return jsonify({'config': default_config})
        
        with open(routing_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        return jsonify({'config': config})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routing/config', methods=['POST'])
def save_routing_config():
    """Persist the model routing configuration."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No configuration data provided'}), 400
        
        # Validate required fields
        required_fields = ['mode', 'modelMappings', 'configMappings']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate routing mode
        if data['mode'] not in ['default', 'model-mapping', 'config-mapping']:
            return jsonify({'error': 'Invalid routing mode'}), 400
        
        # Ensure mapping entries exist for each service
        for service in ['claude', 'codex']:
            if service not in data['modelMappings']:
                data['modelMappings'][service] = []
            if service not in data['configMappings']:
                data['configMappings'][service] = []
        
        routing_config_file = DATA_DIR / 'model_router_config.json'
        
        # Persist the configuration to disk
        with open(routing_config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return jsonify({'success': True, 'message': 'Routing configuration saved successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-connection', methods=['POST'])
def test_connection():
    """Test connectivity to an upstream API endpoint."""
    try:
        data = request.get_json()
        service = data.get('service')
        model = data.get('model')
        base_url = data.get('base_url')
        auth_token = data.get('auth_token')
        api_key = data.get('api_key')
        extra_params = data.get('extra_params', {})

        # Parameter validation
        if not service:
            return jsonify({'error': 'Missing service parameter'}), 400
        if not model:
            return jsonify({'error': 'Missing model parameter'}), 400
        if not base_url:
            return jsonify({'error': 'Missing base_url parameter'}), 400

        if service not in ['claude', 'codex']:
            return jsonify({'error': 'Invalid service name'}), 400

        # Require at least one authentication method
        if not auth_token and not api_key:
            return jsonify({'error': 'Missing authentication (auth_token or api_key)'}), 400

        # Retrieve the relevant proxy instance
        if service == 'claude':
            from src.claude.proxy import proxy_service
        else:
            from src.codex.proxy import proxy_service

        # Execute the connectivity probe
        result = proxy_service.test_endpoint(
            model=model,
            base_url=base_url,
            auth_token=auth_token,
            api_key=api_key,
            extra_params=extra_params
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({
            'success': False,
            'status_code': None,
            'response_text': str(e),
            'target_url': None,
            'error_message': str(e)
        }), 500

# =============== Model Settings (Codex) ===============

@app.get('/api/codex/settings')
def api_get_codex_settings():
    """Return saved defaults for Codex reasoning effort, verbosity, and summary."""

    try:
        cfg = load_system_config()
        codex_defaults = cfg.get('codexDefaults', {}) if isinstance(cfg, dict) else {}
        efforts = codex_defaults.get('reasoningEffortByModel', {}) if isinstance(codex_defaults, dict) else {}
        verbosity = codex_defaults.get('verbosityByModel', {}) if isinstance(codex_defaults, dict) else {}
        summaries = codex_defaults.get('summaryByModel', {}) if isinstance(codex_defaults, dict) else {}

        if not efforts:
            efforts = {
                'gpt-5': 'medium',
                'gpt-5-codex': 'medium'
            }
        else:
            codex_effort = efforts.get('gpt-5-codex')
            if isinstance(codex_effort, str) and codex_effort.lower() == 'minimal':
                efforts['gpt-5-codex'] = 'medium'

        if not verbosity:
            verbosity = {
                'gpt-5': 'medium',
                'gpt-5-codex': 'medium'
            }

        if not summaries:
            summaries = {
                'gpt-5': 'auto',
                'gpt-5-codex': 'auto'
            }

        return jsonify({
            'effortByModel': efforts,
            'verbosityByModel': verbosity,
            'summaryByModel': summaries
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/codex/settings')
def api_set_codex_settings():
    """Persist Codex reasoning effort, verbosity, and summary defaults."""

    try:
        data = request.get_json(silent=True) or {}
        efforts = data.get('effortByModel') or {}
        verbosity = data.get('verbosityByModel') or {}
        summaries = data.get('summaryByModel') or {}

        if not isinstance(efforts, dict):
            return jsonify({'error': 'effortByModel must be an object'}), 400
        if not isinstance(verbosity, dict):
            return jsonify({'error': 'verbosityByModel must be an object'}), 400
        if not isinstance(summaries, dict):
            return jsonify({'error': 'summaryByModel must be an object'}), 400

        allowed_effort = {'minimal', 'low', 'medium', 'high'}
        allowed_verbosity = {'low', 'medium', 'high'}
        allowed_summary = {'off', 'auto', 'detailed'}

        normalized_effort = {}
        for model, value in efforts.items():
            if not isinstance(value, str) or value.lower() not in allowed_effort:
                return jsonify({'error': f'Invalid effort for model {model}; must be minimal/low/medium/high'}), 400
            normalized_effort[str(model)] = value.lower()

        if normalized_effort.get('gpt-5-codex') == 'minimal':
            normalized_effort['gpt-5-codex'] = 'medium'

        normalized_verbosity = {}
        for model, value in verbosity.items():
            if not isinstance(value, str) or value.lower() not in allowed_verbosity:
                return jsonify({'error': f'Invalid verbosity for model {model}; must be low/medium/high'}), 400
            normalized_verbosity[str(model)] = value.lower()

        normalized_summary = {}
        for model, value in summaries.items():
            if not isinstance(value, str) or value.lower() not in allowed_summary:
                return jsonify({'error': f'Invalid summary setting for model {model}; must be off/auto/detailed'}), 400
            normalized_summary[str(model)] = value.lower()

        for model_id in ('gpt-5', 'gpt-5-codex'):
            normalized_summary.setdefault(model_id, 'auto')

        cfg = load_system_config()
        if not isinstance(cfg, dict):
            cfg = {}
        codex_defaults = cfg.setdefault('codexDefaults', {})
        codex_defaults['reasoningEffortByModel'] = normalized_effort
        codex_defaults['verbosityByModel'] = normalized_verbosity
        codex_defaults['summaryByModel'] = normalized_summary
        save_system_config(cfg)

        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/codex/build-body', methods=['POST'])
def build_codex_body():
    """Build a valid OpenAI Responses body for Codex CLI-style calls.

    Request JSON: {"prompt": str, "model": str (default gpt-5), "reasoning_effort": str?}
    Returns: {"json": <body>, "headers": {...}}
    """
    try:
        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return jsonify({'error': 'Missing prompt'}), 400
        model = data.get('model') or 'gpt-5'
        reasoning_effort = (data.get('reasoning_effort') or 'medium').strip()
        requested_summary = (data.get('reasoning_summary') or '').strip().lower()

        cfg = load_system_config()
        codex_defaults = cfg.get('codexDefaults', {}) if isinstance(cfg, dict) else {}
        summary_defaults = codex_defaults.get('summaryByModel', {}) if isinstance(codex_defaults, dict) else {}
        default_summary = summary_defaults.get(model, 'auto')

        if model == 'gpt-5-codex' and reasoning_effort.lower() == 'minimal':
            reasoning_effort = 'medium'

        body = {
            'model': model,
            'instructions': INSTRUCTIONS_CLI,
            'tool_choice': 'auto',
            'parallel_tool_calls': False,
            'reasoning': {'effort': reasoning_effort},
            'store': False,
            'stream': True,
            'include': ['reasoning.encrypted_content'],
            'prompt_cache_key': str(uuid.uuid4()),
            'input': [
                {
                    'type': 'message',
                    'role': 'user',
                    'content': [
                        {'type': 'input_text', 'text': prompt}
                    ]
                }
            ]
        }
        headers = {
            'Accept': 'text/event-stream',
            'OpenAI-Beta': 'responses=experimental',
            'Content-Type': 'application/json'
        }
        summary_choice = requested_summary or default_summary or ''
        summary_choice = summary_choice.lower()
        if summary_choice in {'auto', 'detailed'}:
            body['reasoning']['summary'] = summary_choice
        elif summary_choice == 'off' or not summary_choice:
            body['reasoning'].pop('summary', None)
        else:
            return jsonify({'error': 'Invalid reasoning.summary; supported values: auto, detailed, off'}), 400

        # Drop keys with None and any optional fields the upstream rejects
        allowed = {
            'model','instructions','tool_choice','parallel_tool_calls',
            'reasoning','store','stream','include','prompt_cache_key','input'
        }
        body = {k: v for k, v in body.items() if v is not None and k in allowed}
        return jsonify({'json': body, 'headers': headers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/codex/quick-send', methods=['POST'])
def quick_send_codex():
    """Send a prompt through the local Codex proxy and return the first SSE lines (for curl-less testing).

    Request JSON: {"prompt": str, "model": str?, "max_lines": int?}
    Returns: {status_code, lines: [str]}
    """
    try:
        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return jsonify({'error': 'Missing prompt'}), 400
        model = data.get('model') or 'gpt-5'
        max_lines = int(data.get('max_lines') or 60)

        # Build body and headers
        build_resp = app.test_client().post('/api/codex/build-body', json={'prompt': prompt, 'model': model})
        if build_resp.status_code != 200:
            return build_resp
        payload = build_resp.get_json() or {}
        body = payload.get('json')
        headers = payload.get('headers') or {}

        # Send to the local Codex proxy (will forward to active upstream)
        # Use /v1/responses so that upstream base_url can be https://gaccode.com/codex
        url = 'http://127.0.0.1:3211/v1/responses'
        lines: list[str] = []
        # Avoid zstd/gzip streaming decode issues: request identity encoding.
        headers_stream = dict(headers)
        headers_stream['Accept-Encoding'] = 'identity'
        with requests.post(url, headers=headers_stream, json=body, stream=True, timeout=(10, 30)) as r:
            try:
                for i, raw in enumerate(r.iter_lines(decode_unicode=True)):
                    if raw is None:
                        continue
                    lines.append(raw)
                    if i + 1 >= max_lines:
                        break
            finally:
                pass
        return jsonify({'status_code': 200, 'lines': lines})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/loadbalance/config', methods=['GET'])
def get_loadbalance_config():
    """Retrieve the load-balancer configuration."""
    try:
        lb_config_file = DATA_DIR / 'lb_config.json'

        def default_section():
            return {
                'failureThreshold': 3,
                'currentFailures': {},
                'excludedConfigs': []
            }

        default_config = {
            'mode': 'active-first',
            'services': {
                'claude': default_section(),
                'codex': default_section()
            }
        }

        if not lb_config_file.exists():
            return jsonify({'config': default_config})

        with open(lb_config_file, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)

        config = {
            'mode': raw_config.get('mode', 'active-first'),
            'services': {
                'claude': default_section(),
                'codex': default_section()
            }
        }

        for service in ['claude', 'codex']:
            section = raw_config.get('services', {}).get(service, {})
            threshold = section.get('failureThreshold', section.get('failover_count', 3))
            try:
                threshold = int(threshold)
                if threshold <= 0:
                    threshold = 3
            except (TypeError, ValueError):
                threshold = 3

            failures = section.get('currentFailures', section.get('current_failures', {}))
            if not isinstance(failures, dict):
                failures = {}
            normalized_failures = {}
            for name, count in failures.items():
                try:
                    numeric = int(count)
                except (TypeError, ValueError):
                    numeric = 0
                normalized_failures[str(name)] = max(numeric, 0)

            excluded = section.get('excludedConfigs', section.get('excluded_configs', []))
            if not isinstance(excluded, list):
                excluded = []
            normalized_excluded = [str(item) for item in excluded if isinstance(item, str)]

            config['services'][service] = {
                'failureThreshold': threshold,
                'currentFailures': normalized_failures,
                'excludedConfigs': normalized_excluded,
            }

        return jsonify({'config': config})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/loadbalance/config', methods=['POST'])
def save_loadbalance_config():
    """Persist the load-balancer configuration."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No configuration data provided'}), 400

        mode = data.get('mode')
        if mode not in ['active-first', 'weight-based']:
            return jsonify({'error': 'Invalid loadbalance mode'}), 400

        services = data.get('services', {})
        normalized = {
            'mode': mode,
            'services': {}
        }

        for service in ['claude', 'codex']:
            section = services.get(service, {})
            threshold = section.get('failureThreshold', 3)
            try:
                threshold = int(threshold)
                if threshold <= 0:
                    threshold = 3
            except (TypeError, ValueError):
                return jsonify({'error': f'Invalid failureThreshold for service {service}'}), 400

            failures = section.get('currentFailures', {})
            if not isinstance(failures, dict):
                return jsonify({'error': f'currentFailures for service {service} must be an object'}), 400
            normalized_failures = {}
            for name, count in failures.items():
                try:
                    numeric = int(count)
                except (TypeError, ValueError):
                    return jsonify({'error': f'Failure count for {service}:{name} must be integer'}), 400
                normalized_failures[str(name)] = max(numeric, 0)

            excluded = section.get('excludedConfigs', [])
            if excluded is None:
                excluded = []
            if not isinstance(excluded, list):
                return jsonify({'error': f'excludedConfigs for service {service} must be an array'}), 400
            normalized_excluded = [str(item) for item in excluded if isinstance(item, str)]

            normalized['services'][service] = {
                'failureThreshold': threshold,
                'currentFailures': normalized_failures,
                'excludedConfigs': normalized_excluded
            }

        lb_config_file = DATA_DIR / 'lb_config.json'

        with open(lb_config_file, 'w', encoding='utf-8') as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'message': 'Load-balancer configuration saved successfully'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/loadbalance/reset-failures', methods=['POST'])
def reset_loadbalance_failures():
    """Reset load-balancer failure counters."""
    try:
        data = request.get_json()
        service = data.get('service')
        config_name = data.get('config_name')  # Optional; when absent reset all

        if not service or service not in ['claude', 'codex']:
            return jsonify({'error': 'Invalid service parameter'}), 400

        lb_config_file = DATA_DIR / 'lb_config.json'

        # If the configuration file does not exist, nothing needs to be reset
        if not lb_config_file.exists():
            return jsonify({'success': True, 'message': 'No reset required'})

        with open(lb_config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        services = config.setdefault('services', {})
        service_config = services.setdefault(service, {
            'failureThreshold': 3,
            'currentFailures': {},
            'excludedConfigs': []
        })

        current_failures = service_config.setdefault('currentFailures', {})
        excluded_configs = service_config.setdefault('excludedConfigs', [])

        if config_name:
            key = str(config_name)
            if key in current_failures:
                current_failures[key] = 0
            if key in excluded_configs:
                excluded_configs.remove(key)
            message = f'Reset failure counters for {service}:{key}'
        else:
            service_config['currentFailures'] = {}
            service_config['excludedConfigs'] = []
            message = f'Reset all failure counters for {service}'

        with open(lb_config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'message': message})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/system/config', methods=['GET'])
def get_system_config():
    """Retrieve the system configuration."""
    try:
        config = load_system_config()
        return jsonify({'config': config})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/system/config', methods=['POST'])
def update_system_config():
    """Update and persist the system configuration."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No configuration data provided'}), 400

        # Validate logLimit
        log_limit = data.get('logLimit')
        if log_limit is not None:
            if not isinstance(log_limit, int) or log_limit not in [10, 30, 50, 100]:
                return jsonify({'error': 'Invalid logLimit value'}), 400

        # Persist the configuration
        save_system_config(data)

        # Apply trimming immediately if logLimit changed
        if log_limit is not None:
            trim_logs_to_limit(log_limit)

        return jsonify({'success': True, 'message': 'System configuration saved successfully'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

def start_ui_server(port=3300):
    """Start the UI server in development mode and open a browser."""
    print(f"Starting Web UI server on port {port}")

    # Launch the Flask application
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    start_ui_server()
