#!/bin/bash
# EC2 instance: c8a.12xlarge (48 vCPUs, 96GB RAM, AMD EPYC 5th gen)
#   Instance ID: i-02d0c8c6970c9915f
#   IP: 44.211.98.254
#
# Launch (or reattach):  ./run.sh --host ec2-user@44.211.98.254 --agents 3
# Detach:                Ctrl-b d
# Reattach:              ssh -t ec2-user@44.211.98.254 'tmux attach -t maxsat'
# Switch agent windows:  Ctrl-b n (next) / Ctrl-b p (prev) / Ctrl-b <number>
# Kill:                  ssh ec2-user@44.211.98.254 'tmux kill-session -t maxsat'
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST=""
NUM_AGENTS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --agents) NUM_AGENTS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [ -z "$HOST" ] || [ -z "$NUM_AGENTS" ]; then
  echo "Usage: $0 --host <user@host> --agents <n>"
  exit 1
fi

REPO_URL="${REPO_URL:-$(git -C "$SCRIPT_DIR" remote get-url origin)}"
GIT_USER_NAME="${GIT_USER_NAME:-$(git config user.name)}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-$(git config user.email)}"

# Ensure clone URL includes token for authenticated access on remote
if [[ "$REPO_URL" == https://github.com/* && -n "${GITHUB_ACCESS_TOKEN:-}" && "$REPO_URL" != *@* ]]; then
  REPO_URL="${REPO_URL/https:\/\/github.com/https://${GITHUB_ACCESS_TOKEN}@github.com}"
fi

# Refresh API key from local Claude Code login if available
if [ -f "$HOME/.claude.json" ]; then
  KEY=$(python3 -c "import json; print(json.load(open('$HOME/.claude.json'))['primaryApiKey'])")
  if grep -q "^CLAUDE_CODE_API_KEY=" "$SCRIPT_DIR/.env" 2>/dev/null; then
    sed -i '' "s|^CLAUDE_CODE_API_KEY=.*|CLAUDE_CODE_API_KEY=\"$KEY\"|" "$SCRIPT_DIR/.env"
  else
    echo "CLAUDE_CODE_API_KEY=\"$KEY\"" >> "$SCRIPT_DIR/.env"
  fi
fi

ssh "$HOST" "test -f ~/.env" 2>/dev/null || scp "$SCRIPT_DIR/.env" "$HOST":~/

# Provision and launch agents on remote
ssh "$HOST" "NUM_AGENTS=$NUM_AGENTS REPO_URL='$REPO_URL' GIT_USER_NAME='$GIT_USER_NAME' GIT_USER_EMAIL='$GIT_USER_EMAIL' bash -s" < "$SCRIPT_DIR/setup.sh"
ssh -t "$HOST" 'tmux attach -t maxsat'
