#!/usr/bin/env bash
# Mock Claude CLI - simulates claude --print behavior
# Outputs stream-json format that the daemon expects.
#
# Behavior:
#   - Receives message via --print <msg> argument
#   - Echoes the message back wrapped in proper stream-json events
#   - Supports --output-format stream-json, --resume, --verbose flags
#   - Special messages trigger specific behaviors:
#     "echo:<text>"        -> echoes <text> back
#     "error"              -> exits with code 1
#     "slow"               -> sleeps 3s then responds
#     "tools"              -> simulates a tool_use event
#     anything else        -> echoes the message back

set -euo pipefail

# Parse arguments
MESSAGE=""
SESSION_ID="mock-session-$(date +%s)"
RESUME_ID=""
OUTPUT_FORMAT=""
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --print)
            MESSAGE="$2"
            shift 2
            ;;
        --output-format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --resume)
            RESUME_ID="$2"
            SESSION_ID="$RESUME_ID"
            shift 2
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --dangerously-skip-permissions)
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [[ -z "$MESSAGE" ]]; then
    echo '{"type":"error","message":"No message provided"}' >&2
    exit 1
fi

# Emit system init event
echo "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"$SESSION_ID\",\"model\":\"mock-claude-1.0\"}"

# Handle special messages
case "$MESSAGE" in
    echo:*)
        REPLY="${MESSAGE#echo:}"
        ;;
    error)
        echo '{"type":"error","message":"Simulated error"}' >&2
        exit 1
        ;;
    slow)
        sleep 3
        REPLY="Slow response completed"
        ;;
    tools)
        # Simulate a tool_use event
        echo "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"tool_use\",\"name\":\"Read\",\"input\":{\"file_path\":\"/tmp/test.txt\"},\"id\":\"tool_1\"}]}}"
        # Then a text response
        echo "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"I read the file.\"}]}}"
        echo "{\"type\":\"result\",\"session_id\":\"$SESSION_ID\",\"duration_ms\":100}"
        exit 0
        ;;
    *)
        REPLY="Mock response: $MESSAGE"
        ;;
esac

# Emit streaming partial events (simulate typing)
for word in $REPLY; do
    echo "{\"type\":\"stream_event\",\"event\":{\"type\":\"content_block_delta\",\"delta\":{\"text\":\"$word \"}}}"
done

# Emit complete assistant message
echo "{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"$REPLY\"}]}}"

# Emit result event with session_id
echo "{\"type\":\"result\",\"session_id\":\"$SESSION_ID\",\"duration_ms\":42}"
