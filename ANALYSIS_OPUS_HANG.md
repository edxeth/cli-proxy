# Technical Analysis: Droid CLI Hanging with Opus Models

## Executive Summary

**Root Cause Identified**: The `_ChatCompletionsSseTransformer.flush()` method in `/home/devkit/projects/cli_proxy/src/legacy/proxy.py` has a critical edge case where **empty or null content with tool_calls can produce malformed SSE output** that causes Droid CLI's parser to hang indefinitely.

## Critical Code Section

Lines 663-805 in `src/legacy/proxy.py` - The `_ChatCompletionsSseTransformer.flush()` method

### The Bug

**Location**: Lines 793-794
```python
if text_value:
    delta['content'] = text_value
```

**Problem**: When a response has `tool_calls` but the `content` field is `None` or empty, the delta dictionary only contains `role` and `tool_calls`, but **no `content` field at all**. 

According to OpenAI's SSE spec, when a model makes a tool call, the first chunk should include:
1. Role in delta
2. Tool calls in delta  
3. **`content` field (can be empty string or null)**

**Why Opus hangs but Sonnet doesn't**:
- Sonnet responses typically include text content alongside tool_calls
- Opus responses more frequently return **tool_calls without any text content**
- Droid CLI's SSE parser expects a `content` field to be present in all chunks
- Without this field, Droid's parser waits indefinitely for the content field to appear

## Evidence from Code Review

### 1. Buffer Size Limits (Lines 76-77 in base_proxy.py)
```python
self.max_logged_response_bytes = 1024 * 1024  # 1MB
```
- ✅ No issue here - 1MB is sufficient for Opus responses
- Both Sonnet and Opus responses are well under this limit

### 2. Timeout Configuration (Lines 102-108 in base_proxy.py)
```python
timeout = httpx.Timeout(
    timeout=None,
    connect=30.0,
    read=None,  # No read timeout - allows infinite streaming
    write=30.0,
    pool=None,
)
```
- ✅ No timeout limits on reading responses
- This allows Opus's longer responses to complete
- Not the cause of the hang

### 3. Keep-Alive Settings (Line 825 in legacy/proxy.py)
```python
timeout_keep_alive=60
```
- ✅ 60 seconds is sufficient
- Opus responses complete in ~5 seconds according to docs

### 4. SSE Format Generation (Lines 737-755)
```python
def _chunk(delta: Dict[str, Any], finish_reason: Optional[str], 
           include_usage: bool = False, extra: Optional[Dict[str, Any]] = None) -> str:
    payload = {
        'id': upstream.get('id') or f"chatcmpl-{uuid.uuid4().hex}",
        'object': 'chat.completion.chunk',
        'created': created_at,
        'model': model_name,
        'choices': [
            {
                'index': 0,
                'delta': delta or {},  # This is where empty dict causes issues
                'finish_reason': finish_reason
            }
        ]
    }
```
- ⚠️ **Issue**: `delta` can be `{'role': 'assistant', 'tool_calls': [...]}` without `content`
- Droid expects **all deltas to have a content field** (even if empty)

### 5. Response Parsing Logic (Lines 670-723)
The transformer tries to handle both JSON and SSE input formats:

```python
try:
    upstream = json.loads(buffer_text)
except Exception:
    # Try parsing as SSE stream
    chunks = buffer_text.split('\n')
    tool_calls_found = None
    last_choice = None
    last_message = {}
```

- ✅ This logic is sound and handles both formats
- Not the root cause

### 6. Content Extraction (Lines 779-794)
```python
# Extract text content safely, handling various formats
text_value = ''
if isinstance(content, str):
    text_value = content
elif isinstance(content, list):
    parts = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get('text'), str):
            parts.append(item['text'])
        elif isinstance(item, str):
            parts.append(item)
    text_value = ''.join(parts)
# If content is None or any other type, text_value stays empty string

if text_value:  # ⚠️ BUG: Only adds content if non-empty
    delta['content'] = text_value
```

**THE BUG**: When `content` is `None` or empty, `text_value` becomes `''` (empty string), and the `if text_value:` check fails. This means **no `content` field is added to delta**.

### 7. Tool Calls Handling (Lines 796-799)
```python
tool_calls = message_block.get('tool_calls')
if isinstance(tool_calls, list) and tool_calls:
    delta['tool_calls'] = tool_calls
```
- ✅ Tool calls are correctly added to delta
- But without the `content` field, the chunk is incomplete

## OpenAI SSE Specification

According to OpenAI's streaming format, a tool-calling chunk should look like:

```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1234567890,"model":"claude-opus-4","choices":[{"index":0,"delta":{"role":"assistant","content":"","tool_calls":[{"id":"call_abc","type":"function","function":{"name":"test","arguments":"{}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1234567890,"model":"claude-opus-4","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

**Key observation**: The `content` field is **always present** in the first chunk, even if it's an empty string.

## Why Droid Hangs

1. Droid CLI receives the first SSE chunk with `delta: {role: 'assistant', tool_calls: [...]}`
2. Droid's parser sees tool_calls but **no content field**
3. Droid assumes more chunks are coming to fill in the content
4. No more chunks arrive (because flush() already sent `[DONE]`)
5. Droid waits indefinitely for the content field to appear
6. Timeout after 15+ seconds

## Why Roo Code Works

Roo Code likely has a more lenient SSE parser that:
- Doesn't require `content` to be present
- Treats missing `content` as empty string
- Focuses on `finish_reason` to determine stream completion

## Why Sonnet Works

Sonnet responses typically include thinking/reasoning text even when making tool calls:
```json
{
  "content": "I'll use the Read tool to check that file.",
  "tool_calls": [...]
}
```

This means `text_value` is non-empty, the `if text_value:` check passes, and `content` is added to delta.

## The Fix

**Location**: Line 793-794 in `src/legacy/proxy.py`

**Current code**:
```python
if text_value:
    delta['content'] = text_value
```

**Fixed code**:
```python
# Always include content field for OpenAI compatibility
# Empty string is valid when tool_calls are present
delta['content'] = text_value if text_value else ''
```

Or more explicitly:
```python
# OpenAI SSE spec requires content field in all chunks
delta['content'] = text_value or ''
```

This ensures that **every delta has a content field**, even if it's an empty string, which matches OpenAI's specification and prevents Droid's parser from hanging.

## Testing the Fix

After applying the fix, test with:

```bash
# Should now work without hanging
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "List files in current directory"

# Verify debug output shows complete response
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "test" --output-format debug
```

Expected behavior:
- Opus responses complete successfully
- Tool calls are properly parsed
- No hanging or timeouts
- Identical behavior to Sonnet

## Additional Recommendations

1. **Add content field validation test**: Create a unit test that validates all SSE chunks include a `content` field
2. **Log delta structure**: Add debug logging to see the delta structure being generated
3. **Compare with OpenAI reference**: Validate transformer output matches OpenAI's official SSE format byte-for-byte
4. **Test with other models**: Verify fix doesn't break other model responses

## References

- OpenAI Streaming API: https://platform.openai.com/docs/api-reference/streaming
- Proxy code: `/home/devkit/projects/cli_proxy/src/legacy/proxy.py` lines 640-805
- Issue documentation: `/home/devkit/projects/cli_proxy/docs/OPUS_DROID_ISSUE.md`
- Related commits: 91168a1, 589a8e8, 43f3e1f
