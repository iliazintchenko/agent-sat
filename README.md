```
    _                    _   ____    _  _____
   / \   __ _  ___ _ __ | |_/ ___|  / \|_   _|
  / _ \ / _` |/ _ \ '_ \| __\___ \ / _ \ | |
 / ___ \ (_| |  __/ | | | |_ ___) / ___ \| |
/_/   \_\__, |\___|_| |_|\__|____/_/   \_\_|
        |___/
```

An autonomous AI agent that teaches itself to become the world's top expert on MaxSAT. Given 229 weighted MaxSAT instances from the [2024 MaxSAT Evaluation](https://maxsat-evaluations.github.io/2024/) (main anytime weighted track), it discovers novel strategies, finds better solutions and iteratively refines its toolbox.

## How it works

1. An AI agent (e.g. Claude Code) reads `program.md` for instructions
2. It reads `expert.md` for accumulated knowledge from prior runs
3. It reads `library/` for the tools it has so far
4. It runs solvers on instances, discovers what works, updates everything
5. It commits and pushes to this repo so other agents can build on its findings

```
                              ┌────────────────────┐
                              │     Agent Brain    │
                              │                    │
                              │ expert.md          │
                              │ library/           │
                              │ best-solutions.bin │
                              │ experiments.log    │
                              └───────┬────────────┘
                        git pull/push │
                 ┌────────────┬───────┴─────────┬─────────────┐
                 │            │                 │             │
          ┌──────▼──────┐ ┌───▼────────┐ ┌──────▼──────┐     ...
          │   VM  1     │ │   VM  2    │ │   VM  3     │
          │             │ │            │ │             │
          │ ┌─────────┐ │ │ ┌────────┐ │ │ ┌─────────┐ │
          │ │ Agent 1 │ │ │ │Agent 3 │ │ │ │ Agent 5 │ │
          │ │ ┌─┬─┬─┐ │ │ │ │┌─┬─┬─┐ │ │ │ │ ┌─┬─┬─┐ │ │
          │ │ │S│S│S│ │ │ │ ││S│S│S│ │ │ │ │ │S│S│S│ │ │
          │ │ └─┴─┴─┘ │ │ │ │└─┴─┴─┘ │ │ │ │ └─┴─┴─┘ │ │
          │ ├─────────┤ │ │ ├────────┤ │ │ ├─────────┤ │
          │ │ Agent 2 │ │ │ │Agent 4 │ │ │ │ Agent 6 │ │
          │ │ ┌─┬─┬─┐ │ │ │ │┌─┬─┬─┐ │ │ │ │ ┌─┬─┬─┐ │ │
          │ │ │S│S│S│ │ │ │ ││S│S│S│ │ │ │ │ │S│S│S│ │ │
          │ │ └─┴─┴─┘ │ │ │ │└─┴─┴─┘ │ │ │ │ └─┴─┴─┘ │ │
          │ └─────────┘ │ │ └────────┘ │ │ └─────────┘ │
          └─────────────┘ └────────────┘ └─────────────┘

          S = solver process (python)
```

```bash
# Launch on EC2 (handles everything: installs deps, clones repo,
# downloads benchmarks from Helsinki, launches agents in tmux)
./run.sh --host ec2-user@<ip> --agents 3
```

Requires a `.env` file with `CLAUDE_CODE_OAUTH_TOKEN` and `GITHUB_ACCESS_TOKEN`.

To generate the OAuth token (uses your Claude Pro/Max subscription, not API billing):
```bash
claude setup-token
```
This creates a long-lived token (valid 1 year) that lets headless EC2 agents bill against your subscription. It does not rotate or invalidate existing tokens.

## Local setup

```bash
# Install Python dependencies
pip install python-sat numpy

# Download benchmarks from Helsinki (~2GB compressed)
mkdir -p benchmarks/max-sat-2024/mse24-anytime-weighted
curl -L -o /tmp/mse24.zip https://www.cs.helsinki.fi/group/coreo/MSE2024-instances/mse24-anytime-weighted.zip
unzip -o /tmp/mse24.zip -d benchmarks/max-sat-2024/
cd benchmarks/max-sat-2024 && for f in *.wcnf.xz; do xz -d "$f" && mv "${f%.xz}" mse24-anytime-weighted/; done
cd ../..

# Create .env with your tokens
cat > .env << 'EOF'
CLAUDE_CODE_OAUTH_TOKEN="your-token-here"   # from `claude setup-token`
GITHUB_ACCESS_TOKEN="your-token-here"
EOF
```

Multiple agents can work on the same repo simultaneously, communicating through git — each agent pulls the latest solutions and expert knowledge, builds on what others found, and pushes its own improvements. No coordination needed beyond `git pull` and `git push`.

## Results so far

| Metric | Count |
|--------|-------|
| Instances solved | **220 / 229** |
| Optimal (matching competition best) | **30** |
| **Better than competition** | **5** |
| Novel solve (no known solution existed) | **1** |
| Within 1.1x of reference | 123 |
| Within 1.5x | 183 |
| Within 2x | 209 |
| Unsolved | 9 |

### Beat the 2024 MaxSAT Competition

| Instance | Our cost | Competition best | Improvement |
|----------|----------|-----------------|-------------|
| switchingactivity_74 | 10 | 16 | **37.5% better** |
| synplicate dag_run2_10_size_11 | 374 | 518 | **27.8% better** |
| synplicate dag_run2_16_size_9 | 333 | 398 | **16.3% better** |
| switchingactivity_68 | 8 | 9 | **11.1% better** |
| BTBNSL hailfinder_10000 | 49,986,819,152 | 50,007,681,202 | **0.04% better** |
| pseudoBoolean mod010 | 8,081 | no solution | **novel solve** |

### Hardest remaining

| Instance | Ratio | Why it's hard |
|----------|-------|---------------|
| relational-inference pa-1 | 603x | 2.5M vars, 1.1M soft clauses |
| twitter | 9.7x | 51K softs, 9.7M hard clauses |
| causal-discovery Water | 4.4x | 8.3M cost vs 1.9M ref |
| timetabling test4 | 3.3x | 131K vars |
| decision-tree tic-tac-toe | 2.6x | adaboost ensemble |

9 instances remain unsolved — mostly >16M variables or no known reference solution.

## Techniques discovered

The agent developed these approaches autonomously, discovering what works through experimentation:

| Technique | Best for | Key insight |
|-----------|----------|-------------|
| **Greedy SAT** with selector variables | Few soft clauses (<500) | Heaviest-first greedy with CaDiCaL assumptions |
| **Core-guided search** | Unit soft clauses | Iterative UNSAT core relaxation. comp07.lp: 1778x → **optimal** |
| **WPM1 core-guided** | Weighted unit softs | Proper relaxation variables + at-most-one constraints |
| **Biased-SAT** | Breaking local optima | Random assumption subsets produce diverse solutions |
| **Clause-weighting LS** (SATLike) | Stuck at local optima | Dynamic weight adjustment escapes single-flip traps |
| **Tabu search** | No-hard / unit-soft instances | With SAT init + restarts. judgment-aggregation: 49x → 1.5x |
| **Multi-init** | Diverse starting points | Different solvers (CaDiCaL, glucose4, MiniCard) + random assumptions |
| **Alternating CWLS + WalkSAT** | Deep optimization | Alternating phases for continuous improvement. pa-1: 5445x → 612x |
| **RC2 with CaDiCaL** | Weighted unit softs | Previously dismissed, but `solver='cd19'` finds optimals. haplotyping-12: 3.8x → 1.01x |

## Library

All code the agent writes lives in `library/`:

| Module | Functions | Purpose |
|--------|-----------|---------|
| `solvers.py` | `greedy_sat`, `tabu_search`, `multi_init`, `sat_init`, `walksat_hard`, `walksat_soft`, `sat_solve_with_timeout` | Core solver building blocks |
| `core_guided.py` | `core_guided_solve`, `core_guided_budget`, `wpm1_solve` | UNSAT core-based optimization for unit soft clauses |
| `clause_weight_ls.py` | `clause_weight_local_search` | SATLike-inspired dynamic clause weighting |
| `solutions.py` | `load_solutions`, `update_solution`, `get_best_costs` | Compressed solution storage (1.7GB → 1.5MB) |
| `wcnf_parser.py` | `parse_wcnf`, `evaluate_cost`, `check_hard_clauses` | Single-pass streaming WCNF parser |

## Known limitations

- **Low parallelism**: Claude Code rarely launches more than 6 parallel scripts, and often runs just 1-2 at a time, leaving most cores idle on large machines.
- **Tunnel vision**: The agent can fixate on grinding one instance for hours (e.g. pa-1 from 5445x to 612x over many rounds) while ignoring easier wins elsewhere.
- **Session length**: Despite "never stop" instructions, the agent tends to wrap up after a few hours, deciding it has reached a natural stopping point.

The agent maintains `expert.md` as a living knowledge base and improves the library as it learns.
