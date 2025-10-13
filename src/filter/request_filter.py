import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional


class RequestFilter:
    """Request filter for sanitising request payloads."""
    
    def __init__(self):
        self.filter_file = Path.home() / '.clp' / 'filter.json'
        self.rules = []
    
    def load_rules(self):
        """Load filter rules from filter.json."""
        try:
            if self.filter_file.exists():
                with open(self.filter_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                if isinstance(data, list):
                    self.rules = data
                elif isinstance(data, dict):
                    self.rules = [data]
                else:
                    self.rules = []
            else:
                self.rules = []
                
        except (json.JSONDecodeError, IOError) as e:
            print(f"Failed to load filter rules: {e}")
            self.rules = []
    
    def apply_filters(self, data: bytes) -> bytes:
        """
        Apply filter rules to the request payload.

        Args:
            data: Raw request payload (bytes)

        Returns:
            Filtered request payload (bytes)
        """
        if not self.rules or not data:
            return data
        
        try:
            # Convert bytes into a string for processing
            content = data.decode('utf-8', errors='ignore')
            
            # Apply each filter rule
            for rule in self.rules:
                if not isinstance(rule, dict):
                    continue
                    
                source = rule.get('source', '')
                target = rule.get('target', '')
                op = rule.get('op', 'replace')
                
                if not source:
                    continue
                
                if op == 'replace':
                    # Replace occurrences with the provided target
                    content = content.replace(source, target)
                elif op == 'remove':
                    # Remove occurrences by replacing with an empty string
                    content = content.replace(source, '')
            
            # Convert back to bytes
            return content.encode('utf-8')
            
        except Exception as e:
            print(f"Request filter processing failed: {e}")
            return data
    
    def reload_rules(self):
        """Reload filter rules from disk."""
        self.load_rules()

# Global filter instance
request_filter = RequestFilter()

def filter_request_data(data: bytes) -> bytes:
    """
    Convenience helper for filtering request data.

    Args:
        data: Raw request payload

    Returns:
        Filtered request payload
    """
    request_filter.load_rules()
    return request_filter.apply_filters(data)

def reload_filter_rules():
    """Convenience wrapper to reload filter rules."""
    request_filter.reload_rules()