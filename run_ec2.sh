#!/bin/bash
# EC2 instance: c8a.12xlarge (48 vCPUs, 96GB RAM, AMD EPYC 5th gen)
#   Instance ID: i-02d0c8c6970c9915f
#   IP: 44.211.98.254
#
# Launch (or reattach):  ./run_ec2.sh --host ec2-user@44.211.98.254 --agents 3
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

# Convert SSH URL to HTTPS and inject token for authenticated access on remote
if [[ "$REPO_URL" == git@github.com:* ]]; then
  REPO_URL="https://github.com/${REPO_URL#git@github.com:}"
fi
if [[ "$REPO_URL" == https://github.com/* && -n "${GITHUB_ACCESS_TOKEN:-}" && "$REPO_URL" != *@* ]]; then
  REPO_URL="${REPO_URL/https:\/\/github.com/https://${GITHUB_ACCESS_TOKEN}@github.com}"
fi

# Refresh API key from local Claude Code login if available
if [ -f "$HOME/.claude.json" ]; then
  KEY=$(python3 -c "import json; print(json.load(open('$HOME/.claude.json'))['primaryApiKey'])")
  if grep -q "^CLAUDE_CODE_API_KEY=" "$SCRIPT_DIR/.env" 2>/dev/null; then
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i '' "s|^CLAUDE_CODE_API_KEY=.*|CLAUDE_CODE_API_KEY=\"$KEY\"|" "$SCRIPT_DIR/.env"
    else
      sed -i "s|^CLAUDE_CODE_API_KEY=.*|CLAUDE_CODE_API_KEY=\"$KEY\"|" "$SCRIPT_DIR/.env"
    fi
  else
    echo "CLAUDE_CODE_API_KEY=\"$KEY\"" >> "$SCRIPT_DIR/.env"
  fi
fi

scp "$SCRIPT_DIR/.env" "$HOST":~/

# Provision and launch agents on remote
ssh "$HOST" "bash -s $(printf '%q %q %q %q' "$NUM_AGENTS" "$REPO_URL" "$GIT_USER_NAME" "$GIT_USER_EMAIL")" <<'REMOTE'
set -e
NUM_AGENTS="$1"; REPO_URL="$2"; GIT_USER_NAME="$3"; GIT_USER_EMAIL="$4"

source ~/.env
export ANTHROPIC_API_KEY="$CLAUDE_CODE_API_KEY"

# Install system dependencies
if ! command -v python3.14 &> /dev/null || ! command -v git &> /dev/null; then
  sudo dnf install -y python3.14 python3.14-pip git unzip
fi

# Install Claude Code if not present
if ! command -v claude &> /dev/null; then
  curl -fsSL https://claude.ai/install.sh | bash
fi

# Make python3.14 the default (symlink in /usr/local/bin to avoid breaking dnf)
sudo ln -sf /usr/bin/python3.14 /usr/local/bin/python
sudo ln -sf /usr/bin/python3.14 /usr/local/bin/python3

# Install Python dependencies
python3.14 -m pip install -q python-sat numpy 2>/dev/null || true

# Claude Code settings
mkdir -p ~/.claude
printf '%s\n' '{"permissions":{"defaultMode":"bypassPermissions"},"model":"opus[1m]","effortLevel":"max","skipDangerousModePermissionPrompt":true}' > ~/.claude/settings.json

BENCH_DIR="/tmp/agent-sat-benchmarks"

# Download benchmarks once into a shared location
if [ ! -d "$BENCH_DIR/max-sat-2024/mse24-anytime-weighted" ]; then
  mkdir -p "$BENCH_DIR/max-sat-2024/mse24-anytime-weighted"
  curl -L -o /tmp/mse24.zip https://www.cs.helsinki.fi/group/coreo/MSE2024-instances/mse24-anytime-weighted.zip
  unzip -o /tmp/mse24.zip -d "$BENCH_DIR/max-sat-2024/"
  cd "$BENCH_DIR/max-sat-2024"
  for f in *.wcnf.xz; do xz -d "$f" && mv "${f%.xz}" mse24-anytime-weighted/; done
  rm -f /tmp/mse24.zip
fi

# Clone repos — each agent gets its own directory
for i in $(seq 1 "$NUM_AGENTS"); do
  REPO_DIR="/tmp/agent-sat-$i"
  if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull
  else
    git clone "$REPO_URL" "$REPO_DIR"
  fi
  [ -n "$GIT_USER_NAME" ] && git -C "$REPO_DIR" config user.name "$GIT_USER_NAME"
  [ -n "$GIT_USER_EMAIL" ] && git -C "$REPO_DIR" config user.email "$GIT_USER_EMAIL"
  # Symlink shared benchmarks into each clone
  rm -rf "$REPO_DIR/benchmarks/max-sat-2024/mse24-anytime-weighted"
  mkdir -p "$REPO_DIR/benchmarks/max-sat-2024"
  ln -sf "$BENCH_DIR/max-sat-2024/mse24-anytime-weighted" "$REPO_DIR/benchmarks/max-sat-2024/mse24-anytime-weighted"
done

# If already running, just reattach
if tmux has-session -t maxsat 2>/dev/null; then
  exit 0
fi

# Install tmux if not present
if ! command -v tmux &> /dev/null; then
  sudo dnf install -y tmux
fi

# Launch tmux session — one window per agent
for i in $(seq 1 "$NUM_AGENTS"); do
  REPO_DIR="/tmp/agent-sat-$i"
  LOG="$REPO_DIR/agent.log"
  if [ "$i" -eq 1 ]; then
    tmux new-session -s maxsat -n "agent-$i" -d \
      "cd $REPO_DIR && claude -p 'Read program.md and go.' --dangerously-skip-permissions --verbose --output-format stream-json 2>&1 | tee $LOG"
  else
    tmux new-window -t maxsat -n "agent-$i" \
      "cd $REPO_DIR && claude -p 'Read program.md and go.' --dangerously-skip-permissions --verbose --output-format stream-json 2>&1 | tee $LOG"
  fi
  # Add monitoring panes: costs bottom-left, agent steps bottom-right
  tmux split-window -v -t "maxsat:agent-$i" \
    "tail -f $LOG | jq -r 'select(.type==\"assistant\" and .message.usage) | .message.usage | \"in: \\(.input_tokens) cache: \\(.cache_read_input_tokens) out: \\(.output_tokens)\"'"
  tmux split-window -h -t "maxsat:agent-$i.1" \
    "tail -f $LOG | jq -r 'select(.type==\"assistant\" and .message.content) | .message.content[] | select(.type==\"text\") | .text' 2>/dev/null"
  tmux select-pane -t "maxsat:agent-$i.0"
done
REMOTE

ssh -t "$HOST" 'tmux attach -t maxsat'
