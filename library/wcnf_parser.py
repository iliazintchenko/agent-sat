"""Parser for WCNF (2022+ format) MaxSAT instances."""

import json
from pathlib import Path


def parse_wcnf(filepath: str | Path) -> dict:
    """Parse a WCNF file (2022+ format, no p-line).

    Returns dict with keys:
        - nvars: int, number of variables (from header, or computed from clauses)
        - hard_clauses: list of lists of ints (each clause is a list of signed literals)
        - soft_clauses: list of (weight: int, clause: list of ints)
        - metadata: dict of instance statistics from the JSON comment header (if present)
    """
    hard_clauses = []
    soft_clauses = []
    metadata = {}
    max_var = 0
    json_lines = []
    in_json = False

    filepath = Path(filepath)
    with open(filepath) as f:
        for line in f:
            if not line:
                continue
            if line[0] == "c":
                if len(line) > 1 and line[1] == "}":
                    json_lines.append("}")
                    in_json = False
                elif len(line) > 1 and line[1] == "{":
                    in_json = True
                    json_lines.append("{")
                elif in_json and len(line) > 1:
                    json_lines.append(line[1:])
                continue

            parts = line.split()
            if not parts:
                continue

            if parts[0] == "h":
                lits = [int(x) for x in parts[1:-1]]
                hard_clauses.append(lits)
                for lit in lits:
                    v = abs(lit)
                    if v > max_var:
                        max_var = v
            else:
                weight = int(parts[0])
                lits = [int(x) for x in parts[1:-1]]
                soft_clauses.append((weight, lits))
                for lit in lits:
                    v = abs(lit)
                    if v > max_var:
                        max_var = v

    if json_lines:
        try:
            metadata = json.loads("\n".join(json_lines))
        except json.JSONDecodeError:
            pass

    nvars = metadata.get("nvars", max_var)

    return {
        "nvars": nvars,
        "hard_clauses": hard_clauses,
        "soft_clauses": soft_clauses,
        "metadata": metadata,
    }


def evaluate_cost(solution: list[int], soft_clauses: list[tuple[int, list[int]]]) -> int:
    """Compute MaxSAT cost: sum of weights of unsatisfied soft clauses.

    Args:
        solution: list of signed literals representing the assignment
                  (positive = true, negative = false)
        soft_clauses: list of (weight, clause) from parse_wcnf
    Returns:
        cost (lower is better)
    """
    true_lits = set(solution)
    cost = 0
    for weight, clause in soft_clauses:
        if not any(lit in true_lits for lit in clause):
            cost += weight
    return cost


def check_hard_clauses(solution: list[int], hard_clauses: list[list[int]]) -> list[int]:
    """Return indices of violated hard clauses (empty list = all satisfied)."""
    true_lits = set(solution)
    violated = []
    for i, clause in enumerate(hard_clauses):
        if not any(lit in true_lits for lit in clause):
            violated.append(i)
    return violated
