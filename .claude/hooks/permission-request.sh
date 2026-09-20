#!/bin/bash
# PermissionRequest hook: Auto-allow Bash so Claude CLI doesn't prompt every time.
# - Most Bash commands → allow (no dialog)
# - git push → defer to user (no JSON output = show permission dialog)
#
# Add to ~/.claude/settings.json under "hooks"."PermissionRequest" (see hooks/README.md).

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""')
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // .tool_input.description // ""')

# Only handle Bash; other tools are not matched if you use matcher "Bash"
if [[ "$TOOL_NAME" != "Bash" ]]; then
  exit 0
fi

# Defer to user for git push (do not output JSON → permission dialog is shown)
if echo "$CMD" | grep -qE 'git\s+push'; then
  exit 0
fi

# Auto-allow all other Bash commands
printf '%s\n' '{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow"
    }
  }
}'
exit 0
