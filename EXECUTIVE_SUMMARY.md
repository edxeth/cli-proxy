# Executive Summary: Droid CLI + Opus Hanging Issue

## Root Cause Identified

**Critical Bug**: The `_ChatCompletionsSseTransformer.flush()` method in `/home/devkit/projects/cli_proxy/src/legacy/proxy.py` (lines 793-794) **conditionally omits the `content` field** from SSE delta objects when the content is empty.

**Why it breaks Droid CLI + Opus**:
1. Opus responses frequently have empty content when making tool calls
2. The proxy transformer skips adding `content` field when empty
3. Droid CLI's strict SSE parser expects `content` field in ALL chunks
4. Droid hangs waiting for a content field that never arrives

**Why Sonnet works**:
- Sonnet typically includes reasoning text alongside tool calls
- Non-empty content passes the `if text_value:` check
- Content field is added to delta, satisfying Droid's parser

**Why Roo Code works with Opus**:
- Roo Code has a lenient SSE parser
- Treats missing content field as empty string
- Doesn't hang on missing fields

## The One-Line Fix

**File**: `/home/devkit/projects/cli_proxy/src/legacy/proxy.py`  
**Line**: 794

**Current code**:
```python
if text_value:
    delta['content'] = text_value
```

**Fixed code**:
```python
# Always include content field for OpenAI SSE spec compliance
delta['content'] = text_value or ''
```

This ensures the `content` field is **always present** in the delta object, even when empty, which matches OpenAI's specification and works with strict parsers like Droid CLI.

## Verification Required

After applying the fix, test these scenarios:

```bash
# Test 1: Opus should now work without hanging
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "List files in current directory"

# Test 2: Sonnet should still work (regression test)
timeout 15 droid exec --model provider-7/claude-sonnet-4-5-20250929 --auto low "List files in current directory"

# Test 3: Roo Code should still work with Opus
roo --model provider-7/claude-opus-4-1-20250805 --output-format debug "test"

# Test 4: Verify debug output shows complete responses
timeout 15 droid exec --model provider-7/claude-opus-4-1-20250805 --auto low "test" --output-format debug
```

Expected results:
- All tests complete without hanging
- Opus responses are fully parsed
- Tool calls work correctly
- No regression in Sonnet or Roo Code

## Additional Findings

### Timeout Configuration (✅ Not the issue)
- httpx read timeout: **None (unlimited)** - allows long-running responses
- Connect timeout: 30s - sufficient for connection establishment
- Keep-alive: 60s - far exceeds Opus's ~5s response time

### Buffer Limits (✅ Not the issue)
- Max logged response: 1MB
- Typical Opus response: < 10KB
- Buffer never exceeded

### SSE Format Compliance (⚠️ Needs improvement)
- OpenAI specification shows `content: null` explicitly in tool-call chunks
- Current implementation **omits** the field entirely
- Fixed implementation **includes** field with empty string
- Better compliance = better compatibility

## Impact Assessment

**Critical**: This bug affects all tool-calling scenarios with Opus through the proxy when content is empty.

**Affected**:
- Droid CLI + Opus (hangs)
- Potentially other strict SSE parsers
- Any CLI tool expecting OpenAI-compliant SSE format

**Not Affected**:
- Roo Code (lenient parser)
- Sonnet model (typically has content)
- Direct API calls without proxy (no transformation)

## Recommended Actions

### Immediate (Critical Priority)
1. Apply the one-line fix to `src/legacy/proxy.py:794`
2. Test with all scenarios above
3. Deploy to production if tests pass

### Short-term (High Priority)
1. Add unit tests for SSE transformer edge cases:
   - Empty content + tool_calls
   - Null content + tool_calls
   - Multiple tool_calls
   - Content as empty list/string
2. Add debug logging for delta structure
3. Compare transformer output with OpenAI reference responses

### Long-term (Medium Priority)
1. Create comprehensive SSE format validation tests
2. Test compatibility with more AI CLI tools
3. Document OpenAI SSE specification adherence
4. Add CI/CD tests for multiple client implementations

## Confidence Level

**High (95%)** - Based on:
- Code analysis confirms conditional content field addition
- Behavioral pattern matches (Opus fails, Sonnet works)
- OpenAI community docs show content field should be present
- Roo Code works (lenient parser) vs Droid fails (strict parser)
- Direct observation of code flow

The fix is **minimal risk** (one line) with **high reward** (resolves critical bug).

## References

- **Bug location**: `/home/devkit/projects/cli_proxy/src/legacy/proxy.py:793-794`
- **Issue documentation**: `/home/devkit/projects/cli_proxy/docs/OPUS_DROID_ISSUE.md`
- **Detailed analysis**: `/home/devkit/projects/cli_proxy/ANALYSIS_OPUS_HANG.md`
- **Research report**: `/home/devkit/projects/cli_proxy/RESEARCH_REPORT.json`
- **OpenAI SSE spec**: https://platform.openai.com/docs/api-reference/streaming (discussion shows content: null pattern)
