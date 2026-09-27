#!/usr/bin/env bash
set -euo pipefail

EXTRA_ARGS=()
PROVIDER=""
MODEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="$2"
      shift 2
      ;;
    --model=*)
      MODEL="${1#--model=}"
      shift
      ;;
    --provider)
      PROVIDER="$2"
      shift 2
      ;;
    --provider=*)
      PROVIDER="${1#--provider=}"
      shift
      ;;
    --bundle)
      EXTRA_ARGS+=("--bundle" "$2")
      shift 2
      ;;
    --bundle=*)
      EXTRA_ARGS+=("--bundle" "${1#--bundle=}")
      shift
      ;;
    --print|--verbose|--dangerously-skip-permissions|--disable-slash-commands)
      shift
      ;;
    --output-format|--allowedTools|--disallowedTools|--tools|--system-prompt|--resume)
      shift 2
      ;;
    --output-format=*|--allowedTools=*|--disallowedTools=*|--tools=*|--system-prompt=*|--resume=*)
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$MODEL" ]]; then
  if [[ "$MODEL" == *:* && -z "$PROVIDER" ]]; then
    PROVIDER="${MODEL%%:*}"
    MODEL="${MODEL#*:}"
  fi
  if [[ "$PROVIDER" == "openai" && "$MODEL" == "trinity-free" ]]; then
    MODEL="arcee-ai/trinity-large-preview:free"
  fi
fi

if [[ -n "$PROVIDER" ]]; then
  EXTRA_ARGS+=("--provider" "$PROVIDER")
fi
if [[ -n "$MODEL" ]]; then
  EXTRA_ARGS+=("--model" "$MODEL")
fi

PROMPT=$(cat)
if [[ -z "$PROMPT" ]]; then
  echo "error: empty prompt passed to amplifier executor" >&2
  exit 1
fi

BUNDLE="${AMPLIFIER_BUNDLE:-speedrift}"
exec amplifier run --mode single --output-format json --bundle "$BUNDLE" "${EXTRA_ARGS[@]+${EXTRA_ARGS[@]}}" "$PROMPT"
