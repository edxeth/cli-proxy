# Tool Calls Streaming Issue - Investigation Report

## Problem Statement

Droid CLI with image reading (Read tool) is returning `[object Object],[object Object]` as the tool result value instead of actual image data. This breaks the ability to analyze images when using the proxy.

### Symptom
```json
{
  "type": "tool_result",
  "id": "toolu_01...",
  "value": "[object Object],[object Object]",
  "isError": false
}
```

## Investigation Timeline

### Session Start
- User reported tool calls not working with streaming enabled in proxy
- Streaming setting was set to `Streaming: ON` and `Tool Calls Streaming: OFF`

### Root Cause Analysis Performed

1. **Proxy Streaming Logic** (Lines 476-492 in `src/legacy/proxy.py`)
   - When tools are present, proxy sends `stream=False` to A4F upstream (A4F doesn't support streaming with tools)
   - If client requests streaming, proxy transforms JSON response to SSE format
   - Tool calls are correctly extracted and included in SSE delta (Line 825): `delta['tool_calls'] = tool_calls`

2. **SSE Transformer** (Lines 747-832 in `src/legacy/proxy.py`)
   - Correctly extracts tool_calls from A4F response
   - Includes tool_calls in delta for each SSE chunk
   - Verified working with simple test tool calls (TodoWrite tool works fine)

3. **Image Tool Result Injection** (Lines 35-120 in `src/legacy/proxy.py`)
   - `_wrap_inject_image_tool_results()` function designed to:
     - Find Read tool calls in message history
     - Extract file paths from tool call arguments
     - Read image files from disk
     - Convert to data URLs
     - Inject data URLs into tool result messages
   - This function is called on EVERY chat/completions request (Line 527)

### Critical Finding

**Tested original commit `8247c6c` (before any recent changes):**
```bash
git checkout 8247c6c -- src/legacy/proxy.py
```

Result: **EXACT SAME ISSUE** - Tool result still shows `[object Object],[object Object]`

This proves the issue is NOT caused by changes made in this session.

## Timeline of Proxy Changes in This Session

1. **e2a3b6b** - Fixed tool-aware streaming logic (restored from broken commit 58c11ed)
2. **cd10d63** - Added tool_calls_streaming UI toggle
3. **904b37d** - Added UI toggle to Merged and Interactive modes
4. **7a21eb2** - Fixed accept header to always request JSON
5. **5689ba8** - Changed to never transform tool responses to SSE
6. **08b8833** - Changed to transform tool responses to SSE when client requests
7. **0dda6f7** - Changed to ALWAYS transform tool responses to SSE (matches 8247c6c behavior)
8. **f2d1699** - Cleanup of debug logging

All changes maintain or restore working behavior from commit 8247c6c, which ALSO exhibits the `[object Object]` issue.

## Proxy Behavior Verification

When tools are present in a request, the proxy correctly:
- ✅ Sends `stream=False` to A4F (required for tool support)
- ✅ Receives JSON response with tool_calls field
- ✅ Extracts tool_calls array from response
- ✅ Includes tool_calls in SSE chunks sent to client
- ✅ Passes through tool results received from client

Logs confirm:
```
stream: False, site_streaming: True, client_requested: True, has_tools: True, will_transform_sse: True
Response: tool_calls=1 tools, content_len=54, delta_keys=['role', 'content', 'tool_calls']
```

## Tool Result Value Analysis

The value `[object Object],[object Object]` is JavaScript's default `Object.toString()` representation.

This indicates:
1. Droid's Read tool successfully executes and reads the image file
2. Droid receives image data as JavaScript objects
3. Droid fails to serialize these objects to JSON
4. Droid sends the string `"[object Object]"` as the tool result

## Key Observations

1. **Non-image tool calls work fine**
   - LS tool results are correctly serialized: `"total 0\ndrwxrwxrwx..."`
   - Only image data fails to serialize

2. **Proxy correctly handles both types**
   - Image results (`[object Object]`) pass through unchanged
   - Text results (LS) pass through correctly
   - Both are included in SSE transformation

3. **The `_wrap_inject_image_tool_results` function**
   - Designed to FIX this issue by replacing image tool results with data URLs
   - Triggered on every request when messages are present
   - BUT: No evidence it's being called (no debug logs show execution)
   - This suggests either:
     - Tool result messages don't have matching tool_call_id in history
     - Tool result messages don't have tool_call with name='Read'
     - The function execution path isn't being reached

## Questions for Investigation

To determine if this is truly a Droid issue vs. a proxy issue:

1. **Does the proxy's `_wrap_inject_image_tool_results` function execute when tool results return?**
   - Need to verify tool_call_map is being built
   - Need to verify tool result messages are being matched
   - Need to verify image files are being read and converted to data URLs

2. **Did Droid's Read tool behavior change?**
   - When did image reading break?
   - What changed between when it worked and now?
   - Is there a Droid version where this works?

3. **Is the tool result actually reaching the proxy?**
   - Or is Droid handling the `[object Object]` locally and not sending to proxy?

## Reproduction Steps

```bash
droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "What do you see in this picture /mnt/c/Users/mysol/OneDrive/Pictures/Screenshots/2025-10/chrome_EaRwgA51jJ.png" --output-format debug
```

Expected (based on it working before):
- Droid calls proxy with image file path
- Proxy extracts file and converts to data URL
- Claude analyzes the image
- Result is returned to Droid

Actual:
- Droid calls proxy with image file path
- Droid's Read tool returns `[object Object],[object Object]`
- Proxy passes this through
- Claude cannot analyze (no image data provided)

## Conclusion

The `[object Object]` issue exists on the original commit `8247c6c`, meaning:

1. **This is NOT a regression from changes in this session**
2. **Something between commit 8247c6c and now (outside this codebase) changed**
   - Possible: Droid version update
   - Possible: Claude API behavior change
   - Possible: JavaScript runtime environment change

To fix this issue, we need to:
1. Verify what changed since it last worked
2. Check if there's a way to detect when Read tool fails to serialize
3. Consider implementing a fallback for serialization failures
4. Or identify why the proxy's image injection isn't executing
