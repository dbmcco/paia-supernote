#!/usr/bin/env bash
# ABOUTME: WorkGraph executor wrapper that drives `pi` (pi-coding-agent) in print mode.
# ABOUTME: Reads the rendered task prompt from stdin (the wg custom-executor contract)
# ABOUTME: and runs it non-interactively via `pi -p --mode json`. Peer of codex-run.sh / claude-run.sh.

set -euo pipefail

# Make repo-local wg shims discoverable.
if [[ -d "$PWD/.workgraph/bin" ]]; then
  export PATH="$PWD/.workgraph/bin:$PATH"
fi

# Parse wg-passed flags. We MUST build a provider-qualified model spec because pi
# resolves a BARE model id (e.g. "glm-5.2" with no provider) via catalog search,
# which is non-deterministic and frequently falls back to the opencode provider
# ("No API key found for opencode" -> exit 1). The deterministic, documented form
# is "provider/id", so when wg gives us --provider + a bare --model we qualify it.
PROVIDER=""
MODEL=""
FALLBACK_MODEL=""
THINKING=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) PROVIDER="$2"; shift 2 ;;
    --provider=*) PROVIDER="${1#--provider=}"; shift ;;
    --model|--selected-model) MODEL="$2"; shift 2 ;;
    --model=*|--selected-model=*) MODEL="${1#*=}"; shift ;;
    --fallback-model) FALLBACK_MODEL="$2"; shift 2 ;;
    --fallback-model=*) FALLBACK_MODEL="${1#--fallback-model=}"; shift ;;
    --thinking) THINKING="$2"; shift 2 ;;
    --thinking=*) THINKING="${1#--thinking=}"; shift ;;
    # Flags we don't need but wg may pass — consume value-pairs and bare flags gracefully.
    --system-prompt) shift 2 ;;
    --system-prompt=*) shift ;;
    --allowedTools|--disallowedTools|--input-format|--output-format) shift 2 ;;
    --allowedTools=*|--disallowedTools=*|--input-format=*|--output-format=*) shift ;;
    --print|--verbose|--dangerously-skip-permissions|--no-session-persistence|--disable-slash-commands) shift ;;
    *) shift ;;
  esac
done

# Normalize the model spec the dispatcher actually sends. The wg dispatcher
# passes a colon-qualified route like "--model zai:glm-5.2" (and may leave an
# executor prefix like "pi:zai:glm-5.2"). pi's CLI cannot resolve a bare
# "zai:glm-5.2"; it needs --provider zai --model glm-5.2 PLUS the WG_PI_*
# bridge env so the @worksgood/pi extension registers the custom endpoint.
# Strip an executor prefix, then split a colon-form spec into separate
# PROVIDER/MODEL so the qualified-build block below fires.
if [[ "$MODEL" == pi:* ]]; then
  MODEL="${MODEL#pi:}"
fi
if [[ -z "$PROVIDER" && -n "$MODEL" && "$MODEL" == *:* ]]; then
  PROVIDER="${MODEL%%:*}"
  MODEL="${MODEL#*:}"
fi

# Build the provider-qualified model spec. When WG supplies a provider and a
# bare model id (e.g. provider=zai, model=glm-5.2), emit separate --provider
# and --model flags so pi can resolve a custom provider via the auto-loaded
# @worksgood/pi extension. Also export the WG Pi bridge env vars the extension
# needs to register the custom endpoint/key. Pass through unchanged if already
# qualified (contains '/') or if no provider was supplied.
MODEL_ARGS=()
SELECTED_MODEL="$MODEL"
if [[ -n "$MODEL" ]]; then
  if [[ "$MODEL" != */* && -n "$PROVIDER" ]]; then
    SELECTED_MODEL="$PROVIDER/$MODEL"
    MODEL_ARGS+=(--provider "$PROVIDER" --model "$MODEL")
    # Bridge custom providers into pi's model registry.
    export WG_PI_PROVIDER="$PROVIDER"
    export WG_MODEL="$PROVIDER:$MODEL"
    case "$PROVIDER" in
      zai)
        export WG_PI_BASE_URL="${WG_PI_BASE_URL:-https://api.z.ai/api/coding/paas/v4}"
        export WG_PI_API_KEY="${WG_PI_API_KEY:-${ZAI_API_KEY:-}}"
        ;;
    esac
  else
    MODEL_ARGS+=(--model "$SELECTED_MODEL")
  fi
fi
if [[ -n "$FALLBACK_MODEL" && "$FALLBACK_MODEL" != */* && -n "$PROVIDER" ]]; then
  FALLBACK_MODEL="$PROVIDER/$FALLBACK_MODEL"
fi
# Pi only receives the selected model; keep fallback metadata available to the
# receipt/audit layer without passing an unsupported flag to the Pi CLI.
export WG_SELECTED_MODEL="$SELECTED_MODEL"
export WG_FALLBACK_MODEL="$FALLBACK_MODEL"
THINKING_ARGS=()
if [[ -n "$THINKING" ]]; then
  THINKING_ARGS+=(--thinking "$THINKING")
fi

# Read the rendered task prompt from stdin (the [executor.prompt_template]).
PROMPT="$(cat)"
if [[ -z "${PROMPT//[[:space:]]/}" ]]; then
  echo "error: empty workgraph prompt" >&2
  exit 1
fi

# Optional prompt-enrichment hook (shared with codex-run.sh / claude-run.sh).
PRE_DISPATCH_HOOK="$PWD/.workgraph/hooks/pre-dispatch.sh"
if [[ -x "$PRE_DISPATCH_HOOK" ]]; then
  ENRICHED=$(printf '%s' "$PROMPT" | "$PRE_DISPATCH_HOOK" 2>/dev/null) || true
  if [[ -n "$ENRICHED" ]]; then
    PROMPT="$ENRICHED"
  fi
fi

# CRITICAL: Suppress the Avery persona and turn-end memory writes for autonomous
# workgraph runs. Without this, every dispatched task would inject Avery's identity
# into the run AND pollute Avery's memory store with task evidence. Codex needs no
# such guard because it has no Avery persona; pi does, so this is non-negotiable.
export PAIA_AVERY="${PAIA_AVERY:-0}"

# Resolve the pi binary. Honor PI_BIN / PI_CMD overrides, else rely on PATH.
PI_BIN="${PI_BIN:-${PI_CMD:-pi}}"

# Run pi non-interactively in streaming JSON mode. The generated Workgraph
# wrapper tees stdout into raw_stream.jsonl for liveness and pi-stream-bridge.
set +e
"$PI_BIN" -p --mode json "${MODEL_ARGS[@]}" "${THINKING_ARGS[@]}" "$PROMPT"
EXIT_CODE=$?
set -e

exit $EXIT_CODE
