#!/bin/bash
# Provisions the EC2 instance — installs dependencies, clones repos, launches agents in tmux.
# Called by run.sh over SSH; not meant to be invoked directly.
#
# Expected env vars: NUM_AGENTS, REPO_URL, GIT_USER_NAME, GIT_USER_EMAIL
set -e

# Load secrets
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
cat > ~/.claude/settings.json <<'EOF'
{"permissions":{"defaultMode":"bypassPermissions"},"model":"opus[1m]","effortLevel":"max","skipDangerousModePermissionPrompt":true}
EOF

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
