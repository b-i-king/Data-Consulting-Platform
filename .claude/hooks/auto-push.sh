#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Auto-push hook for Claude Code (runs after every Bash command Claude runs).
#
# WHAT IT DOES
#   If the command Claude just ran included `git commit`, push the current
#   branch to GitHub (origin) so work is never left only on a temporary
#   cloud machine.
#
# SAFETY RULES
#   * Never pushes main or master — those change only through pull requests.
#   * Never force-pushes.
#   * Does nothing if there is nothing new to push.
#   * Retries network failures 4 times (waiting 2, 4, 8, 16 seconds).
#   * Never blocks Claude: it always exits 0 and reports the result.
#
# Configured in .claude/settings.json (hooks → PostToolUse → Bash).
# To turn it off: delete that entry, or run /hooks in Claude Code.
# ─────────────────────────────────────────────────────────────────────────────

# The hook receives the tool call as JSON on stdin; pull out the shell command.
cmd=$(jq -r '.tool_input.command // ""' 2>/dev/null)

# Only react to commands that made a commit.
[[ "$cmd" =~ git[[:space:]]+commit ]] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0

# Report back to the user (systemMessage) and to Claude (additionalContext).
report() {
  jq -n --arg msg "$1" '{systemMessage: $msg,
    hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $msg}}'
  exit 0
}

case "$branch" in
  HEAD)        exit 0 ;;   # detached HEAD (mid-rebase etc.) — nothing to push
  main|master) report "Auto-push skipped: '$branch' is protected; changes reach it through pull requests." ;;
esac

# Nothing new? (If the branch has no upstream yet, there is always something to push.)
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  [[ $(git rev-list --count '@{u}..HEAD' 2>/dev/null) == 0 ]] && exit 0
fi

delay=2
for attempt in 1 2 3 4 5; do
  if out=$(git push -u origin "$branch" 2>&1); then
    report "Auto-pushed '$branch' to origin."
  fi
  # Retry only network-looking failures; anything else (permissions, rejected
  # non-fast-forward) will not be fixed by waiting.
  echo "$out" | grep -qiE 'could not resolve|timed out|connection|network|unable to access|RPC failed|5[0-9][0-9]' || break
  [[ $attempt -lt 5 ]] && sleep "$delay" && delay=$((delay * 2))
done
report "Auto-push of '$branch' FAILED: $(echo "$out" | tail -n 2 | tr '\n' ' ')"
