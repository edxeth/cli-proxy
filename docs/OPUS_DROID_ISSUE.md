# Known Issue: Droid CLI Hangs with Opus Models

## Status
**UNRESOLVED** - Issue reproduced and debugged, root cause identified as Droid CLI limitation, not proxy issue.

## Symptoms
- `droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "prompt"` hangs indefinitely
- Timeout occurs after 15+ seconds with no response
- Affects Opus model specifically; Sonnet and other models work fine through the same proxy
- **Verified working**: Roo Code with Opus through the proxy ✅
- **Verified working**: Direct curl/requests calls to upstream API ✅

## Investigation Summary

### What Works
1. **Proxy infrastructure**: Proxy correctly routes requests and returns properly formatted SSE responses
2. **Opus with Roo Code**: Roo Code successfully calls Opus through the proxy (proven with `--output-format debug`)
3. **Opus direct API calls**: Direct curl/requests calls to the upstream API work fine
4. **Sonnet with Droid**: Droid works perfectly with Sonnet through the same proxy
5. **Tool calling**: Both Sonnet and Opus correctly return tool_calls in SSE format

### What Doesn't Work
- **Droid + Opus through proxy**: Hangs without returning a response
- **Droid + Opus direct to API**: Also hangs (tested by updating `~/.factory/config.json` to bypass proxy)

### Root Cause Analysis

**Finding 1: Structural Response Equivalence**
- Sonnet and Opus responses from the proxy are **structurally identical**
- Same number of SSE chunks
- Same JSON structure
- Same headers
- Response timing is similar (Sonnet: 3s, Opus: 5s)

**Finding 2: Droid Debug Output**
- With Sonnet: User message → Assistant response (complete)
- With Opus: User message → (hangs, no assistant response received)

This indicates Droid's SSE parser never completes when processing Opus responses.

**Finding 3: Direct API Testing**
When Droid is configured to call the upstream API directly (bypassing proxy):
- Droid + Sonnet direct: Still works
- Droid + Opus direct: Also hangs

This **proves the issue is not in the proxy**, but in how Droid handles Opus responses in streaming mode.

## Possible Root Causes (Droid-side)

1. **Droid has a streaming parser bug** specific to Opus model responses
2. **Droid model detection logic** treats Opus differently (e.g., different timeout for Opus)
3. **Content-type handling** - Droid may be misinterpreting the `text/event-stream` format for Opus
4. **Chunked encoding issue** - Droid's buffer handling for chunked transfers may fail with Opus response patterns
5. **Factory API key/model mapping** - The "provider-7/claude-opus-4-1-20250805" model ID might not be properly recognized by Droid

## Testing Commands

### Reproduce the Issue
```bash
# Test Opus through proxy (hangs)
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "test"

# Test Sonnet through proxy (works)
timeout 15 droid exec --model provider-7/claude-sonnet-4-5-20250929 --auto low "test"

# See debug output (Opus gets stuck)
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "test" --output-format debug
```

### Proxy Response Verification
All responses are well-formed and equivalent:
```python
import requests
import json

payload = {
    "model": "your-model",
    "messages": [{"role": "user", "content": "test"}],
    "stream": True
}

resp = requests.post("http://localhost:3212/chat/completions",
                     json=payload,
                     headers={"Authorization": "Bearer YOUR_KEY"},
                     timeout=15)

# Both Sonnet and Opus return 200 OK with valid SSE format
print(f"Status: {resp.status_code}")  # 200 for both
print(f"Content-Type: {resp.headers['content-type']}")  # text/event-stream for both
print(f"Lines: {len(resp.text.split(chr(10)))}")  # Same structure for both
```

## Workaround

Currently, there is **no workaround** for using Opus with Droid through this proxy. Options:

1. **Use Sonnet instead** (fully functional)
2. **Use Roo Code instead** (works with Opus)
3. **Report to Factory/Droid team** - They need to investigate their streaming parser

## Next Steps for Resolution

1. **Contact Factory (Droid team)** with:
   - Reproduction steps
   - Debug output showing the hang point
   - Confirmation that it's not a proxy issue (since Roo Code works)
   - Test that direct API calls also hang

2. **Droid team should check**:
   - SSE parser implementation for edge cases with Opus
   - How the model field is being processed (might be model-specific routing)
   - Timeout settings or buffer handling that differs per model
   - Factory API integration for "provider-7/*" model paths

3. **If this is a proxy-layer issue** (less likely):
   - Check if there's a model-specific response format difference we haven't detected
   - Add explicit Opus response logging to proxy
   - Compare raw bytes of Sonnet vs Opus responses at the HTTP level

## References

- **Proxy commits**: 91168a1, 589a8e8, 47951b6, 43f3e1f
- **Testing environment**: Droid CLI 0.21.1
- **Models tested**:
  - ✅ provider-7/claude-sonnet-4-5-20250929 (works)
  - ❌ provider-7/claude-opus-4-1-20250805 (hangs)
