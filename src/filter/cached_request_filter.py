#!/usr/bin/env python3
"""Cached request filter that reloads rules when the file changes."""
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any

class CachedRequestFilter:
    """Request filter with cached rules and change detection."""
    
    def __init__(self, cache_check_interval: float = 1.0):
        """
        Initialize the cached filter.

        Args:
            cache_check_interval: Minimum interval between file checks (seconds)
        """
        self.filter_file = Path.home() / '.clp' / 'filter.json'
        self._rules = []
        self._file_mtime = 0
        self._last_check_time = 0
        self.cache_check_interval = cache_check_interval
    
    def _should_reload(self) -> bool:
        """
        Determine whether the rules should be reloaded
        based on the filter file modification time.
        """
        # Rate-limit stat calls to avoid excessive filesystem checks
        current_time = time.time()
        if current_time - self._last_check_time < self.cache_check_interval:
            return False
        
        self._last_check_time = current_time
        
        try:
            if not self.filter_file.exists():
                # File disappeared; clear any cached rules
                if self._rules:
                    self._rules = []
                    self._file_mtime = 0
                    return True
                return False
            
            current_mtime = self.filter_file.stat().st_mtime
            if current_mtime != self._file_mtime:
                return True
            
            return False
        except (OSError, FileNotFoundError):
            return False
    
    def load_rules(self, force: bool = False):
        """
        Load filter rules from disk with caching.

        Args:
            force: Whether to bypass cache checks
        """
        if not force and not self._should_reload():
            return  # Use cached rules
        
        try:
            if not self.filter_file.exists():
                self._rules = []
                self._file_mtime = 0
                return
            
            with open(self.filter_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate and normalise the rules schema
            if isinstance(data, list):
                self._rules = data
            elif isinstance(data, dict):
                self._rules = [data]
            else:
                print("Warning: filter rules must be an object or list of objects")
                self._rules = []
            
            # Pre-compile regex objects where possible
            for rule in self._rules:
                if 'source' in rule and 'regex' not in rule:
                    # Compile to regex when the source uses regex semantics
                    try:
                        rule['regex'] = re.compile(rule['source'].encode('utf-8'), re.DOTALL)
                    except re.error:
                        # Fallback to plain string replacement on failure
                        rule['regex'] = None
            
            # Update the cached modification timestamp
            self._file_mtime = self.filter_file.stat().st_mtime
            
            print(f"Loaded filter rules: {len(self._rules)} entries")
            
        except json.JSONDecodeError as e:
            print(f"Filter rules JSON is invalid: {e}")
            self._rules = []
        except Exception as e:
            print(f"Failed to load filter rules: {e}")
            self._rules = []
    
    def apply_filters(self, data: bytes) -> bytes:
        """
        Apply the cached rules to the request payload.

        Args:
            data: Raw request data

        Returns:
            Filtered request data
        """
        # Ensure rules are loaded (cached)
        self.load_rules()
        
        if not self._rules or not data:
            return data
        
        # Apply each rule
        filtered_data = data
        for rule in self._rules:
            if 'op' not in rule or 'source' not in rule:
                continue
            
            op = rule['op']
            source = rule['source'].encode('utf-8')
            
            if op == 'replace':
                target = rule.get('target', '').encode('utf-8')
                
                # Use the cached regex when available
                if 'regex' in rule and rule['regex']:
                    filtered_data = rule['regex'].sub(target, filtered_data)
                else:
                    # Plain string replacement
                    filtered_data = filtered_data.replace(source, target)
                    
            elif op == 'remove':
                # Removal operation
                if 'regex' in rule and rule['regex']:
                    filtered_data = rule['regex'].sub(b'', filtered_data)
                else:
                    filtered_data = filtered_data.replace(source, b'')
        
        return filtered_data
    
    def get_rules_count(self) -> int:
        """Return the number of currently loaded rules."""
        self.load_rules()  # Ensure rules are up to date
        return len(self._rules)
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """Return the current rule list (read-only copy)."""
        self.load_rules()  # Ensure rules are up to date
        # Return a copy to avoid external mutation
        return [rule.copy() for rule in self._rules if 'regex' not in rule]
    
    def force_reload(self):
        """Force a reload of the rules."""
        self.load_rules(force=True)

# Global instance
request_filter = CachedRequestFilter()

def filter_request_data(data: bytes) -> bytes:
    """
    Compatibility wrapper matching the legacy API.

    Args:
        data: Raw request data

    Returns:
        Filtered request data
    """
    return request_filter.apply_filters(data)