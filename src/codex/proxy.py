#!/usr/bin/env python3
"""Codex proxy service built on the shared base proxy infrastructure."""
import aiohttp
import logging
import datetime
import time
import json
from pathlib import Path
from urllib import request as urllib_request, error as urllib_error

from fastapi.middleware.cors import CORSMiddleware
from ..core.base_proxy import BaseProxyService
from ..config.cached_config_manager import codex_config_manager

_PROMPT_SOURCE_URL = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/gpt_5_codex_prompt.md"
_PROMPT_CACHE_FILE = Path.home() / ".clp" / "data" / "codex_prompt.md"
_PROMPT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
_PROMPT_FALLBACK = """You are Codex, based on GPT-5. You are running as a coding agent in the Codex CLI on a user's computer.

## General

- The arguments to `shell` will be passed to execvp(). Most terminal commands should be prefixed with ["bash", "-lc"].
- Always set the `workdir` param when using the shell function. Do not use `cd` unless absolutely necessary.
- When searching for text or files, prefer using `rg` or `rg --files` respectively because `rg` is much faster than alternatives like `grep`. (If the `rg` command is not found, then use alternatives.)

## Editing constraints

- Default to ASCII when editing or creating files. Only introduce non-ASCII or other Unicode characters when there is a clear justification and the file already uses them.
- Add succinct code comments that explain what is going on if code is not self-explanatory. You should not add comments like "Assigns the value to the variable", but a brief comment might be useful ahead of a complex code block that the user would otherwise have to spend time parsing out. Usage of these comments should be rare.
- Try to use apply_patch for single file edits, but it is fine to explore other options to make the edit if it does not work well. Do not use apply_patch for changes that are auto-generated (i.e. generating package.json or running a lint or format command like gofmt) or when scripting is more efficient (such as search and replacing a string across a codebase).
- You may be in a dirty git worktree.
    * NEVER revert existing changes you did not make unless explicitly requested, since these changes were made by the user.
    * If asked to make a commit or code edits and there are unrelated changes to your work or changes that you didn't make in those files, don't revert those changes.
    * If the changes are in files you've touched recently, you should read carefully and understand how you can work with the changes rather than reverting them.
    * If the changes are in unrelated files, just ignore them and don't revert them.
- While you are working, you might notice unexpected changes that you didn't make. If this happens, STOP IMMEDIATELY and ask the user how they would like to proceed.
- **NEVER** use destructive commands like `git reset --hard` or `git checkout --` unless specifically requested or approved by the user.

## Plan tool

When using the planning tool:
- Skip using the planning tool for straightforward tasks (roughly the easiest 25%).
- Do not make single-step plans.
- When you made a plan, update it after having performed one of the sub-tasks that you shared on the plan.

## Codex CLI harness, sandboxing, and approvals

The Codex CLI harness supports several different configurations for sandboxing and escalation approvals that the user can choose from.

Filesystem sandboxing defines which files can be read or written. The options for `sandbox_mode` are:
- **read-only**: The sandbox only permits reading files.
- **workspace-write**: The sandbox permits reading files, and editing files in `cwd` and `writable_roots`. Editing files in other directories requires approval.
- **danger-full-access**: No filesystem sandboxing - all commands are permitted.

Network sandboxing defines whether network can be accessed without approval. Options for `network_access` are:
- **restricted**: Requires approval
- **enabled**: No approval needed

Approvals are your mechanism to get user consent to run shell commands without the sandbox. Possible configuration options for `approval_policy` are
- **untrusted**: The harness will escalate most commands for user approval, apart from a limited allowlist of safe "read" commands.
- **on-failure**: The harness will allow all commands to run in the sandbox (if enabled), and failures will be escalated to the user for approval to run again without the sandbox.
- **on-request**: Commands will be run in the sandbox by default, and you can specify in your tool call if you want to escalate a command to run without sandboxing. (Note that this mode is not always available. If it is, you'll see parameters for it in the `shell` command description.)
- **never**: This is a non-interactive mode where you may NEVER ask the user for approval to run commands. Instead, you must always persist and work around constraints to solve the task for the user. You MUST do your utmost best to finish the task and validate your work before yielding. If this mode is paired with `danger-full-access`, take advantage of it to deliver the best outcome for the user. Further, in this mode, your default testing philosophy is overridden: Even if you don't see local patterns for testing, you may add tests and scripts to validate your work. Just remove them before yielding.

When you are running with `approval_policy == on-request`, and sandboxing enabled, here are scenarios where you'll need to request approval:
- You need to run a command that writes to a directory that requires it (e.g. running tests that write to /var)
- You need to run a GUI app (e.g., open/xdg-open/osascript) to open browsers or files.
- You are running sandboxed and need to run a command that requires network access (e.g. installing packages)
- If you run a command that is important to solving the user's query, but it fails because of sandboxing, rerun the command with approval. ALWAYS proceed to use the `with_escalated_permissions` and `justification` parameters - do not message the user before requesting approval for the command.
- You are about to take a potentially destructive action such as an `rm` or `git reset` that the user did not explicitly ask for
- (for all of these, you should weigh alternative paths that do not require approval)

When `sandbox_mode` is set to read-only, you'll need to request approval for any command that isn't a read.

You will be told what filesystem sandboxing, network sandboxing, and approval mode are active in a developer or user message. If you are not told about this, assume that you are running with workspace-write, network sandboxing enabled, and approval on-failure.

Although they introduce friction to the user because your work is paused until the user responds, you should leverage them when necessary to accomplish important work. If the completing the task requires escalated permissions, Do not let these settings or the sandbox deter you from attempting to accomplish the user's task unless it is set to "never", in which case never ask for approvals.

When requesting approval to execute a command that will require escalated privileges:
  - Provide the `with_escalated_permissions` parameter with the boolean value true
  - Include a short, 1 sentence explanation for why you need to enable `with_escalated_permissions` in the justification parameter

## Special user requests

- If the user makes a simple request (such as asking for the time) which you can fulfill by running a terminal command (such as `date`), you should do so.
- If the user asks for a "review", default to a code review mindset: prioritise identifying bugs, risks, behavioural regressions, and missing tests. Findings must be the primary focus of the response - keep summaries or overviews brief and only after enumerating the issues. Present findings first (ordered by severity with file/line references), follow with open questions or assumptions, and offer a change-summary only as a secondary detail. If no findings are discovered, state that explicitly and mention any residual risks or testing gaps.

## Presenting your work and final message

You are producing plain text that will later be styled by the CLI. Follow these rules exactly. Formatting should make results easy to scan, but not feel mechanical. Use judgment to decide how much structure adds value.

- Default: be very concise; friendly coding teammate tone.
- Ask only when needed; suggest ideas; mirror the user's style.
- For substantial work, summarize clearly; follow final‑answer formatting.
- Skip heavy formatting for simple confirmations.
- Don't dump large files you've written; reference paths only.
- No "save/copy this file" - User is on the same machine.
- Offer logical next steps (tests, commits, build) briefly; add verify steps if you couldn't do something.
- For code changes:
  * Lead with a quick explanation of the change, and then give more details on the context covering where and why a change was made. Do not start this explanation with "summary", just jump right in.
  * If there are natural next steps the user may want to take, suggest them at the end of your response. Do not make suggestions if there are no natural next steps.
  * When suggesting multiple options, use numeric lists for the suggestions so the user can quickly respond with a single number.
- The user does not command execution outputs. When asked to show the output of a command (e.g. `git show`), relay the important details in your answer or summarize the key lines so the user understands the result.

### Final answer structure and style guidelines

- Plain text; CLI handles styling. Use structure only when it helps scanability.
- Headers: optional; short Title Case (1-3 words) wrapped in **…**; no blank line before the first bullet; add only if they truly help.
- Bullets: use - ; merge related points; keep to one line when possible; 4–6 per list ordered by importance; keep phrasing consistent.
- Monospace: backticks for commands/paths/env vars/code ids and inline examples; use for literal keyword bullets; never combine with **.
- Code samples or multi-line snippets should be wrapped in fenced code blocks; include an info string as often as possible.
- Structure: group related bullets; order sections general → specific → supporting; for subsections, start with a bolded keyword bullet, then items; match complexity to the task.
- Tone: collaborative, concise, factual; present tense, active voice; self-contained; no "above/below"; parallel wording.
- Don'ts: no nested bullets/hierarchies; no ANSI codes; don't cram unrelated keywords; keep keyword lists short—wrap/reformat if long; avoid naming formatting styles in answers.
- Adaptation: code explanations → precise, structured with code refs; simple tasks → lead with outcome; big changes → logical walkthrough + rationale + next actions; casual one-offs → plain sentences, no headers/bullets.
- File References: When referencing files in your response, make sure to include the relevant start line and always follow the below rules:
  * Use inline code to make file paths clickable.
  * Each reference should have a stand alone path. Even if it's the same file.
  * Accepted: absolute, workspace‑relative, a/ or b/ diff prefixes, or bare filename/suffix.
  * Line/column (1‑based, optional): :line[:column] or #Lline[Ccolumn] (column defaults to 1).
  * Do not use URIs like file://, vscode://, or https://.
  * Do not provide range of lines
  * Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\\repo\\project\\main.rs:12:5
"""


def _load_codex_prompt() -> str:
    """Load the latest Codex prompt, with caching and a safe fallback."""
    logger = logging.getLogger("codex_proxy.prompt_loader")
    cache_path = _PROMPT_CACHE_FILE
    now = time.time()

    try:
        if cache_path.exists():
            if now - cache_path.stat().st_mtime <= _PROMPT_CACHE_TTL_SECONDS:
                return cache_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to read Codex prompt cache metadata: %s", exc)

    try:
        with urllib_request.urlopen(_PROMPT_SOURCE_URL, timeout=10) as response:
            prompt_text = response.read().decode("utf-8")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(prompt_text, encoding="utf-8")
            logger.debug("Fetched latest Codex prompt from %s", _PROMPT_SOURCE_URL)
            return prompt_text
    except (urllib_error.URLError, OSError, ValueError) as exc:
        logger.debug("Unable to fetch Codex prompt from upstream: %s", exc)

    try:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("Codex prompt cache unavailable, using fallback: %s", exc)

    return _PROMPT_FALLBACK


# Minimal CLI-style instructions used for helper builders and parity
INSTRUCTIONS_CLI = _load_codex_prompt()

class CodexProxy(BaseProxyService):
    """Codex proxy service implementation."""

    def __init__(self):
        super().__init__(
            service_name='codex',
            port=3211,
            config_manager=codex_config_manager
        )

        # Allow the UI to connect via CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3300", "http://127.0.0.1:3300"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Configure a dedicated logger
        self.logger = logging.getLogger('codex_proxy')
        self.logger.setLevel(logging.INFO)

        # Add a file handler when none is registered yet
        if not self.logger.handlers:
            log_file = Path.home() / '.clp/run/codex_proxy.log'
            log_file.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.propagate = False

    def test_endpoint(self, model: str, base_url: str, auth_token: str = None, api_key: str = None, extra_params: dict = None) -> dict:
        """Test connectivity against an upstream Codex/OpenAI endpoint."""
        import asyncio
        import aiohttp
        from urllib.parse import urlparse

        # Record the probe start
        self.logger.info(f"Starting Codex API endpoint probe: model={model}, base_url={base_url}")
        start_time = datetime.datetime.now()

        async def _test_connection():
            import uuid

            # Generate a shared session UUID used in multiple headers
            session_uuid = str(uuid.uuid4())

            # Build request headers
            parsed_url = urlparse(base_url)
            host = parsed_url.netloc

            headers = {
                "accept": "text/event-stream",
                "accept-encoding": "gzip",
                "authorization": f'Bearer {auth_token}',
                "connection": "keep-alive",
                "content-type": "application/json",
                "conversation_id": session_uuid,
                "host": host,
                "openai-beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "session_id": session_uuid,
                "user-agent": "codex_cli_rs/0.46.0 (Ubuntu 24.4.0; x86_64) WezTerm/20251005-110037-db5d7437"
            }

            default_effort = self._get_default_effort(model) or 'medium'
            default_summary = self._get_default_summary(model)

            # Construct the baseline OpenAI API request
            openai_body = {
              "model": model,
              "instructions": "You are Codex, based on GPT-5. You are running as a coding agent in the Codex CLI on a user's computer.\n\n## General\n\n- The arguments to `shell` will be passed to execvp(). Most terminal commands should be prefixed with [\"bash\", \"-lc\"].\n- Always set the `workdir` param when using the shell function. Do not use `cd` unless absolutely necessary.\n- When searching for text or files, prefer using `rg` or `rg --files` respectively because `rg` is much faster than alternatives like `grep`. (If the `rg` command is not found, then use alternatives.)\n\n## Editing constraints\n\n- Default to ASCII when editing or creating files. Only introduce non-ASCII or other Unicode characters when there is a clear justification and the file already uses them.\n- Add succinct code comments that explain what is going on if code is not self-explanatory. You should not add comments like \"Assigns the value to the variable\", but a brief comment might be useful ahead of a complex code block that the user would otherwise have to spend time parsing out. Usage of these comments should be rare.\n- You may be in a dirty git worktree.\n    * NEVER revert existing changes you did not make unless explicitly requested, since these changes were made by the user.\n    * If asked to make a commit or code edits and there are unrelated changes to your work or changes that you didn't make in those files, don't revert those changes.\n    * If the changes are in files you've touched recently, you should read carefully and understand how you can work with the changes rather than reverting them.\n    * If the changes are in unrelated files, just ignore them and don't revert them.\n- While you are working, you might notice unexpected changes that you didn't make. If this happens, STOP IMMEDIATELY and ask the user how they would like to proceed.\n\n## Plan tool\n\nWhen using the planning tool:\n- Skip using the planning tool for straightforward tasks (roughly the easiest 25%).\n- Do not make single-step plans.\n- When you made a plan, update it after having performed one of the sub-tasks that you shared on the plan.\n\n## Codex CLI harness, sandboxing, and approvals\n\nThe Codex CLI harness supports several different configurations for sandboxing and escalation approvals that the user can choose from.\n\nFilesystem sandboxing defines which files can be read or written. The options for `sandbox_mode` are:\n- **read-only**: The sandbox only permits reading files.\n- **workspace-write**: The sandbox permits reading files, and editing files in `cwd` and `writable_roots`. Editing files in other directories requires approval.\n- **danger-full-access**: No filesystem sandboxing - all commands are permitted.\n\nNetwork sandboxing defines whether network can be accessed without approval. Options for `network_access` are:\n- **restricted**: Requires approval\n- **enabled**: No approval needed\n\nApprovals are your mechanism to get user consent to run shell commands without the sandbox. Possible configuration options for `approval_policy` are\n- **untrusted**: The harness will escalate most commands for user approval, apart from a limited allowlist of safe \"read\" commands.\n- **on-failure**: The harness will allow all commands to run in the sandbox (if enabled), and failures will be escalated to the user for approval to run again without the sandbox.\n- **on-request**: Commands will be run in the sandbox by default, and you can specify in your tool call if you want to escalate a command to run without sandboxing. (Note that this mode is not always available. If it is, you'll see parameters for it in the `shell` command description.)\n- **never**: This is a non-interactive mode where you may NEVER ask the user for approval to run commands. Instead, you must always persist and work around constraints to solve the task for the user. You MUST do your utmost best to finish the task and validate your work before yielding. If this mode is paired with `danger-full-access`, take advantage of it to deliver the best outcome for the user. Further, in this mode, your default testing philosophy is overridden: Even if you don't see local patterns for testing, you may add tests and scripts to validate your work. Just remove them before yielding.\n\nWhen you are running with `approval_policy == on-request`, and sandboxing enabled, here are scenarios where you'll need to request approval:\n- You need to run a command that writes to a directory that requires it (e.g. running tests that write to /var)\n- You need to run a GUI app (e.g., open/xdg-open/osascript) to open browsers or files.\n- You are running sandboxed and need to run a command that requires network access (e.g. installing packages)\n- If you run a command that is important to solving the user's query, but it fails because of sandboxing, rerun the command with approval. ALWAYS proceed to use the `with_escalated_permissions` and `justification` parameters - do not message the user before requesting approval for the command.\n- You are about to take a potentially destructive action such as an `rm` or `git reset` that the user did not explicitly ask for\n- (for all of these, you should weigh alternative paths that do not require approval)\n\nWhen `sandbox_mode` is set to read-only, you'll need to request approval for any command that isn't a read.\n\nYou will be told what filesystem sandboxing, network sandboxing, and approval mode are active in a developer or user message. If you are not told about this, assume that you are running with workspace-write, network sandboxing enabled, and approval on-failure.\n\nAlthough they introduce friction to the user because your work is paused until the user responds, you should leverage them when necessary to accomplish important work. If the completing the task requires escalated permissions, Do not let these settings or the sandbox deter you from attempting to accomplish the user's task unless it is set to \"never\", in which case never ask for approvals.\n\nWhen requesting approval to execute a command that will require escalated privileges:\n  - Provide the `with_escalated_permissions` parameter with the boolean value true\n  - Include a short, 1 sentence explanation for why you need to enable `with_escalated_permissions` in the justification parameter\n\n## Special user requests\n\n- If the user makes a simple request (such as asking for the time) which you can fulfill by running a terminal command (such as `date`), you should do so.\n- If the user asks for a \"review\", default to a code review mindset: prioritise identifying bugs, risks, behavioural regressions, and missing tests. Findings must be the primary focus of the response - keep summaries or overviews brief and only after enumerating the issues. Present findings first (ordered by severity with file/line references), follow with open questions or assumptions, and offer a change-summary only as a secondary detail. If no findings are discovered, state that explicitly and mention any residual risks or testing gaps.\n\n## Presenting your work and final message\n\nYou are producing plain text that will later be styled by the CLI. Follow these rules exactly. Formatting should make results easy to scan, but not feel mechanical. Use judgment to decide how much structure adds value.\n\n- Default: be very concise; friendly coding teammate tone.\n- Ask only when needed; suggest ideas; mirror the user's style.\n- For substantial work, summarize clearly; follow final‑answer formatting.\n- Skip heavy formatting for simple confirmations.\n- Don't dump large files you've written; reference paths only.\n- No \"save/copy this file\" - User is on the same machine.\n- Offer logical next steps (tests, commits, build) briefly; add verify steps if you couldn't do something.\n- For code changes:\n  * Lead with a quick explanation of the change, and then give more details on the context covering where and why a change was made. Do not start this explanation with \"summary\", just jump right in.\n  * If there are natural next steps the user may want to take, suggest them at the end of your response. Do not make suggestions if there are no natural next steps.\n  * When suggesting multiple options, use numeric lists for the suggestions so the user can quickly respond with a single number.\n- The user does not command execution outputs. When asked to show the output of a command (e.g. `git show`), relay the important details in your answer or summarize the key lines so the user understands the result.\n\n### Final answer structure and style guidelines\n\n- Plain text; CLI handles styling. Use structure only when it helps scanability.\n- Headers: optional; short Title Case (1-3 words) wrapped in **…**; no blank line before the first bullet; add only if they truly help.\n- Bullets: use - ; merge related points; keep to one line when possible; 4–6 per list ordered by importance; keep phrasing consistent.\n- Monospace: backticks for commands/paths/env vars/code ids and inline examples; use for literal keyword bullets; never combine with **.\n- Code samples or multi-line snippets should be wrapped in fenced code blocks; add a language hint whenever obvious.\n- Structure: group related bullets; order sections general → specific → supporting; for subsections, start with a bolded keyword bullet, then items; match complexity to the task.\n- Tone: collaborative, concise, factual; present tense, active voice; self‑contained; no \"above/below\"; parallel wording.\n- Don'ts: no nested bullets/hierarchies; no ANSI codes; don't cram unrelated keywords; keep keyword lists short—wrap/reformat if long; avoid naming formatting styles in answers.\n- Adaptation: code explanations → precise, structured with code refs; simple tasks → lead with outcome; big changes → logical walkthrough + rationale + next actions; casual one-offs → plain sentences, no headers/bullets.\n- File References: When referencing files in your response, make sure to include the relevant start line and always follow the below rules:\n  * Use inline code to make file paths clickable.\n  * Each reference should have a stand alone path. Even if it's the same file.\n  * Accepted: absolute, workspace‑relative, a/ or b/ diff prefixes, or bare filename/suffix.\n  * Line/column (1‑based, optional): :line[:column] or #Lline[Ccolumn] (column defaults to 1).\n  * Do not use URIs like file://, vscode://, or https://.\n  * Do not provide range of lines\n  * Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\\repo\\project\\main.rs:12:5\n",
              "input": [
                {
                  "type": "message",
                  "role": "user",
                  "content": [
                    {
                      "type": "input_text",
                      "text": "<user_instructions>\n\n## Prerequisites\n\n1. Code is written for humans; machines merely execute it.\n2. Think in English, reply in Chinese.\n3. Use MCP tools appropriately.\n\n## Eight Honors and Eight Shames\n\nBe ashamed of guessing at interfaces; take pride in studying them diligently.\nBe ashamed of ambiguous execution; take pride in seeking confirmation.\nBe ashamed of imagining business needs blindly; take pride in verifying with humans.\nBe ashamed of inventing new interfaces; take pride in reusing existing ones.\nBe ashamed of skipping validation; take pride in proactive testing.\nBe ashamed of breaking the architecture; take pride in following conventions.\nBe ashamed of pretending to understand; take pride in honest ignorance.\nBe ashamed of reckless changes; take pride in careful refactoring.\n\n</user_instructions>"
                    }
                  ]
                },
                {
                  "type": "message",
                  "role": "user",
                  "content": [
                    {
                      "type": "input_text",
                      "text": "<environment_context>\n  <cwd>/Users/chagee/te</cwd>\n  <approval_policy>never</approval_policy>\n  <sandbox_mode>danger-full-access</sandbox_mode>\n  <network_access>enabled</network_access>\n  <shell>zsh</shell>\n</environment_context>"
                    }
                  ]
                },
                {
                  "type": "message",
                  "role": "user",
                  "content": [
                    {
                      "type": "input_text",
                      "text": "Your model version"
                    }
                  ]
                }
              ],
              "tools": [
                {
                  "type": "function",
                  "name": "shell",
                  "description": "Runs a shell command and returns its output.",
                  "strict": False,
                  "parameters": {
                    "type": "object",
                    "properties": {
                      "command": {
                        "type": "array",
                        "items": {
                          "type": "string"
                        },
                        "description": "The command to execute"
                      },
                      "justification": {
                        "type": "string",
                        "description": "Only set if with_escalated_permissions is true. 1-sentence explanation of why we want to run this command."
                      },
                      "timeout_ms": {
                        "type": "number",
                        "description": "The timeout for the command in milliseconds"
                      },
                      "with_escalated_permissions": {
                        "type": "boolean",
                        "description": "Whether to request escalated permissions. Set to true if command needs to be run without sandbox restrictions"
                      },
                      "workdir": {
                        "type": "string",
                        "description": "The working directory to execute the command in"
                      }
                    },
                    "required": [
                      "command"
                    ],
                    "additionalProperties": False
                  }
                },
                {
                  "type": "function",
                  "name": "update_plan",
                  "description": "Updates the task plan.\nProvide an optional explanation and a list of plan items, each with a step and status.\nAt most one step can be in_progress at a time.\n",
                  "strict": False,
                  "parameters": {
                    "type": "object",
                    "properties": {
                      "explanation": {
                        "type": "string"
                      },
                      "plan": {
                        "type": "array",
                        "items": {
                          "type": "object",
                          "properties": {
                            "status": {
                              "type": "string",
                              "description": "One of: pending, in_progress, completed"
                            },
                            "step": {
                              "type": "string"
                            }
                          },
                          "required": [
                            "step",
                            "status"
                          ],
                          "additionalProperties": False
                        },
                        "description": "The list of steps"
                      }
                    },
                    "required": [
                      "plan"
                    ],
                    "additionalProperties": False
                  }
                },
                {
                  "type": "function",
                  "name": "view_image",
                  "description": "Attach a local image (by filesystem path) to the conversation context for this turn.",
                  "strict": False,
                  "parameters": {
                    "type": "object",
                    "properties": {
                      "path": {
                        "type": "string",
                        "description": "Local filesystem path to an image file"
                      }
                    },
                    "required": [
                      "path"
                    ],
                    "additionalProperties": False
                  }
                }
              ],
              "tool_choice": "auto",
              "parallel_tool_calls": False,
              "reasoning": {
                "effort": default_effort
              },
              "store": False,
              "stream": True,
              "include": [
                "reasoning.encrypted_content"
              ],
              "prompt_cache_key": session_uuid
            }

            effort_value = default_effort
            if extra_params and extra_params.get('reasoning_effort'):
                effort_value = (extra_params.get('reasoning_effort') or '').strip().lower()
            if effort_value not in {'minimal', 'low', 'medium', 'high'}:
                effort_value = default_effort
            if model == 'gpt-5-codex' and effort_value == 'minimal':
                effort_value = default_effort
            openai_body["reasoning"]["effort"] = effort_value or default_effort

            summary_choice = ''
            if extra_params and extra_params.get('reasoning_summary'):
                summary_choice = (extra_params.get('reasoning_summary') or '').strip().lower()
            if summary_choice not in {'auto', 'detailed'}:
                summary_choice = default_summary
            if summary_choice in {'auto', 'detailed'}:
                openai_body["reasoning"]["summary"] = summary_choice
            else:
                openai_body["reasoning"].pop('summary', None)

            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                keepalive_timeout=60,
                enable_cleanup_closed=True
            )

            session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(
                    total=45,
                    connect=15,
                    sock_read=30
                )
            )

            path = "/responses"
            try:
                # Build the target URL
                target_url = f"{base_url.rstrip('/')}{path}"

                async with session.post(
                    target_url,
                    headers=headers,
                    json=openai_body,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response_text = await response.text()

                    return {
                        'success': response.status == 200,
                        'status_code': response.status,
                        'response_text': response_text,
                        'target_url': target_url,
                        'error_message': None if response.status == 200 else f"HTTP {response.status}: {response.reason}"
                    }
            except Exception as e:
                return {
                    'success': False,
                    'status_code': None,
                    'response_text': str(e),
                    'target_url': f"{base_url.rstrip('/')}{path}",
                    'error_message': str(e)
                }
            finally:
                await session.close()

        # Execute the async probe
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(_test_connection())

        # Log the probe result
        end_time = datetime.datetime.now()
        duration = (end_time - start_time).total_seconds()

        if result['success']:
            self.logger.info(
                f"Codex API endpoint probe succeeded: {result['target_url']}, duration: {duration:.2f}s, status: {result['status_code']}"
            )
        else:
            self.logger.error(
                f"Codex API endpoint probe failed: {result['target_url']}, duration: {duration:.2f}s, error: {result['error_message']}"
            )

        return result

    # To support clients (e.g., Droid/Factory) that omit required Responses headers/fields,
    # force-fill the headers and JSON payload before forwarding.
    def build_target_param(self, path: str, request, body: bytes):  # type: ignore[override]
        target_url, headers, modified_body, active_config_name = super().build_target_param(path, request, body)

        try:
            normalized_path = path.lstrip('/').lower()
            if normalized_path.endswith('responses'):
                # Force mandatory Responses headers
                headers['openai-beta'] = 'responses=experimental'
                headers['accept'] = 'text/event-stream'
                # Avoid upstream compression (zstd/gzip) that downstream clients cannot decode
                headers['accept-encoding'] = 'identity'
                headers.setdefault('content-type', 'application/json')

                # If the upstream base_url lacks /v1 while the client calls /responses,
                # rewrite the path to /v1/responses (e.g. https://.../responses -> .../v1/responses)
                try:
                    if target_url.endswith('/responses') and '/v1/responses' not in target_url:
                        target_url = target_url[:-len('/responses')] + '/v1/responses'
                except Exception:
                    pass

                # Ensure required JSON fields exist: store=false, stream=true, instructions
                import json
                if modified_body:
                    try:
                        payload = json.loads(modified_body.decode('utf-8'))
                        changed = False
                        if not isinstance(payload, dict):
                            payload = {}
                            changed = True
                        input_block = payload.get('input')
                        if isinstance(input_block, str) and input_block.strip():
                            payload['input'] = [{
                                'role': 'user',
                                'content': [{
                                    'type': 'input_text',
                                    'text': input_block
                                }]
                            }]
                            changed = True
                        elif isinstance(input_block, dict):
                            payload['input'] = [input_block]
                            changed = True
                        elif isinstance(input_block, list):
                            # Ensure list entries are dictionaries with required fields
                            normalized_list = []
                            list_changed = False
                            for item in input_block:
                                if isinstance(item, str):
                                    normalized_list.append({
                                        'role': 'user',
                                        'content': [{
                                            'type': 'input_text',
                                            'text': item
                                        }]
                                    })
                                    list_changed = True
                                elif isinstance(item, dict):
                                    normalized_list.append(item)
                                else:
                                    list_changed = True
                            if list_changed:
                                payload['input'] = normalized_list
                                changed = True

                        instructions_value = payload.get('instructions')
                        instructions_text = instructions_value if isinstance(instructions_value, str) else ''
                        host_header = headers.get('host', '')
                        should_override_instructions = False
                        if not instructions_text.strip():
                            should_override_instructions = True
                        elif 'gaccode.com' in host_header and not instructions_text.startswith('You are Codex'):
                            should_override_instructions = True

                        if should_override_instructions:
                            if instructions_text.strip():
                                try:
                                    request.state.codex_original_instructions = instructions_text
                                except AttributeError:
                                    pass
                            payload['instructions'] = INSTRUCTIONS_CLI
                            changed = True

                            if instructions_text.strip():
                                messages = payload.get('input')
                                if not isinstance(messages, list):
                                    messages = []
                                system_message = {
                                    'role': 'system',
                                    'content': [{
                                        'type': 'input_text',
                                        'text': instructions_text
                                    }]
                                }
                                prepend = True
                                if messages:
                                    first = messages[0]
                                    first_content = first.get('content')
                                    if (
                                        first.get('role') == 'system'
                                        and isinstance(first_content, list)
                                        and first_content
                                        and first_content[0].get('text') == instructions_text
                                    ):
                                        prepend = False
                                if prepend:
                                    messages = [system_message] + messages
                                    payload['input'] = messages
                                    changed = True

                        if payload.get('store') is None:
                            payload['store'] = False
                            changed = True
                        if payload.get('stream') is None:
                            payload['stream'] = True
                            changed = True
                        if payload.get('tool_choice') is None:
                            payload['tool_choice'] = 'auto'
                            changed = True

                        model_name = payload.get('model') or ''
                        default_effort = self._get_default_effort(model_name)
                        reasoning_block = payload.get('reasoning') if isinstance(payload.get('reasoning'), dict) else {}
                        if default_effort and reasoning_block.get('effort') != default_effort:
                            reasoning_block['effort'] = default_effort
                            changed = True

                        summary_value = ''
                        if isinstance(reasoning_block, dict):
                            raw_summary = reasoning_block.get('summary')
                            if isinstance(raw_summary, str):
                                normalized_summary = raw_summary.strip().lower()
                                if normalized_summary in {'auto', 'detailed'}:
                                    if normalized_summary != raw_summary:
                                        reasoning_block['summary'] = normalized_summary
                                        changed = True
                                    summary_value = normalized_summary
                                elif normalized_summary in {'off', ''}:
                                    if 'summary' in reasoning_block:
                                        reasoning_block.pop('summary', None)
                                        changed = True
                                else:
                                    if 'summary' in reasoning_block:
                                        reasoning_block.pop('summary', None)
                                        changed = True
                            elif raw_summary is not None:
                                reasoning_block.pop('summary', None)
                                changed = True

                        if not summary_value:
                            default_summary = self._get_default_summary(model_name)
                            if default_summary:
                                reasoning_block['summary'] = default_summary
                                summary_value = default_summary
                                changed = True
                            elif 'summary' in reasoning_block:
                                reasoning_block.pop('summary', None)
                                changed = True

                        if reasoning_block:
                            payload['reasoning'] = reasoning_block
                        elif 'reasoning' in payload:
                            payload['reasoning'] = reasoning_block

                        default_verbosity = self._get_default_verbosity(model_name)
                        text_settings = payload.get('text') if isinstance(payload.get('text'), dict) else {}
                        if text_settings.get('format') != {'type': 'text'}:
                            text_settings['format'] = {'type': 'text'}
                            changed = True
                        if default_verbosity and text_settings.get('verbosity') != default_verbosity:
                            text_settings['verbosity'] = default_verbosity
                            changed = True
                        if text_settings:
                            payload['text'] = text_settings
                        # Remove optional fields that upstream rejects (added by some clients),
                        # e.g. max_output_tokens, service_tier, etc.
                        # Apply a whitelist to stay within the minimal/commonly supported schema.
                        allowed_keys = {
                            'model', 'instructions', 'input', 'tool_choice',
                            'parallel_tool_calls', 'reasoning', 'store', 'stream',
                            'include', 'prompt_cache_key', 'tools', 'text'
                        }
                        # Preserve legacy chat payloads when present so the upstream can validate them
                        if 'messages' in payload:
                            allowed_keys.add('messages')

                        filtered = {k: v for k, v in payload.items() if k in allowed_keys}
                        if filtered != payload:
                            payload = filtered
                            changed = True

                        if changed:
                            modified_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                    except Exception:
                        # If parsing fails, leave the body untouched and rely on header adjustments
                        pass
        except Exception:
            pass

        return target_url, headers, modified_body, active_config_name

    def _get_default_effort(self, model: str) -> str:
        if not model:
            return 'medium'
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return 'medium'
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            effort_map = (
                data.get('codexDefaults', {})
                    .get('reasoningEffortByModel', {})
            )
            if not isinstance(effort_map, dict):
                return 'medium'
            effort = effort_map.get(model)
            value = effort.lower().strip() if isinstance(effort, str) else 'medium'
            if value not in {'minimal', 'low', 'medium', 'high'}:
                value = 'medium'
            if model == 'gpt-5-codex' and value == 'minimal':
                value = 'medium'
            return value
        except Exception:
            return 'medium'

    def _get_default_verbosity(self, model: str) -> str:
        if not model:
            return 'auto'
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return 'auto'
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            verb_map = (
                data.get('codexDefaults', {})
                    .get('verbosityByModel', {})
            )
            if not isinstance(verb_map, dict):
                return ''
            verb = verb_map.get(model)
            verb_value = verb.lower().strip() if isinstance(verb, str) else ''
            return verb_value if verb_value in {'low', 'medium', 'high'} else ''
        except Exception:
            return ''

    def _get_default_summary(self, model: str) -> str:
        if not model:
            return ''
        try:
            import json
            cfg_file = self.data_dir / 'system.json'
            if not cfg_file.exists():
                return ''
            with open(cfg_file, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
            summary_map = (
                data.get('codexDefaults', {})
                    .get('summaryByModel', {})
            )
            if not isinstance(summary_map, dict):
                return 'auto'
            summary = summary_map.get(model)
            summary_value = summary.lower().strip() if isinstance(summary, str) else ''
            return summary_value if summary_value in {'auto', 'detailed'} else 'auto'
        except Exception:
            return 'auto'

    def get_response_chunk_transformer(self, request, path, target_headers, target_body):
        original_instructions = getattr(getattr(request, "state", object()), "codex_original_instructions", None)
        if not original_instructions:
            return None

        if not original_instructions.strip():
            return None

        class _InstructionChunkTransformer:
            def __init__(self, replacement: str):
                self.replacement = replacement
                self.buffer = ''

            def _process_line(self, line: str) -> str:
                if line.startswith('data: '):
                    try:
                        payload = json.loads(line[6:])
                        response_obj = payload.get('response')
                        if isinstance(response_obj, dict):
                            response_obj['instructions'] = self.replacement
                            payload['response'] = response_obj
                            return 'data: ' + json.dumps(payload, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass
                return line

            def process(self, chunk: bytes) -> bytes:
                if chunk:
                    self.buffer += chunk.decode('utf-8', errors='ignore')

                output_lines = []
                while True:
                    newline_index = self.buffer.find('\n')
                    if newline_index == -1:
                        break
                    line, self.buffer = self.buffer[:newline_index], self.buffer[newline_index + 1:]
                    output_lines.append(self._process_line(line))

                if output_lines:
                    return ('\n'.join(output_lines) + '\n').encode('utf-8')
                return b''

            def flush(self) -> bytes:
                if self.buffer:
                    line = self._process_line(self.buffer)
                    self.buffer = ''
                    return (line + '\n').encode('utf-8')
                return b''

        return _InstructionChunkTransformer(original_instructions)

# Global singleton instance
proxy_service = CodexProxy()
app = proxy_service.app

# Routes are registered by BaseProxyService._setup_routes

# build_target_param is implemented in the base class

# log_request is implemented in the base class

def run_app(port=3211):
    """Launch the Codex proxy service."""
    proxy_service.run_app()

if __name__ == '__main__':
    # Run uvicorn directly for local debugging
    import uvicorn

    uvicorn.run(
        app,
        host='0.0.0.0',
        port=3211,
        log_level='info',
        timeout_keep_alive=60,
        http='h11'
    )
