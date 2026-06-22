"""Core MaxSAT solver building blocks: occurrence lists, SAT init, tabu search, greedy SAT, WalkSAT."""

import random
import signal
import time

from pysat.solvers import Solver


class SolverTimeout(Exception):
    pass


def sat_solve_with_timeout(solver, assumptions=None, timeout=30):
    """Call solver.solve() with a wall-clock timeout using SIGALRM.

    SAT solver calls are C-level and don't respect Python time checks.
    SIGALRM interrupts the C code after `timeout` seconds.

    Returns True/False (SAT/UNSAT) or raises SolverTimeout.
    """
    def _handler(signum, frame):
        raise SolverTimeout()

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout)
    try:
        if assumptions:
            result = solver.solve(assumptions=assumptions)
        else:
            result = solver.solve()
        signal.alarm(0)
        return result
    except SolverTimeout:
        signal.alarm(0)
        raise
    finally:
        signal.signal(signal.SIGALRM, old_handler)


def build_occurrence_lists(nvars, hard_clauses, soft_clauses):
    """Build variable-to-clause occurrence lists.

    Returns (hard_pos, hard_neg, soft_pos, soft_neg, soft_weights, soft_vars)
    where *_pos[v] and *_neg[v] are lists of clause indices containing variable v
    positively/negatively.
    """
    hard_pos = [[] for _ in range(nvars + 1)]
    hard_neg = [[] for _ in range(nvars + 1)]
    soft_pos = [[] for _ in range(nvars + 1)]
    soft_neg = [[] for _ in range(nvars + 1)]
    soft_weights = []
    soft_vars = set()

    for i, (w, clause) in enumerate(soft_clauses):
        soft_weights.append(w)
        for lit in clause:
            v = abs(lit)
            soft_vars.add(v)
            if lit > 0:
                soft_pos[v].append(i)
            else:
                soft_neg[v].append(i)

    for i, clause in enumerate(hard_clauses):
        for lit in clause:
            v = abs(lit)
            if lit > 0:
                hard_pos[v].append(i)
            else:
                hard_neg[v].append(i)

    return hard_pos, hard_neg, soft_pos, soft_neg, soft_weights, sorted(soft_vars)


def compute_sat_counts(assignment, hard_clauses, soft_clauses):
    """Compute per-clause satisfaction counts for current assignment.

    Returns (hard_sat_count, soft_sat_count).
    """
    hard_sat_count = [0] * len(hard_clauses)
    soft_sat_count = [0] * len(soft_clauses)

    for i, clause in enumerate(hard_clauses):
        for lit in clause:
            v = abs(lit)
            if (lit > 0 and assignment[v]) or (lit < 0 and not assignment[v]):
                hard_sat_count[i] += 1

    for i, (w, clause) in enumerate(soft_clauses):
        for lit in clause:
            v = abs(lit)
            if (lit > 0 and assignment[v]) or (lit < 0 and not assignment[v]):
                soft_sat_count[i] += 1

    return hard_sat_count, soft_sat_count


def flip_variable(v, assignment, hard_pos, hard_neg, soft_pos, soft_neg,
                  hard_sat_count, soft_sat_count):
    """Flip variable v in assignment and update all sat counts in place."""
    if assignment[v]:
        assignment[v] = 0
        for ci in hard_pos[v]: hard_sat_count[ci] -= 1
        for ci in hard_neg[v]: hard_sat_count[ci] += 1
        for ci in soft_pos[v]: soft_sat_count[ci] -= 1
        for ci in soft_neg[v]: soft_sat_count[ci] += 1
    else:
        assignment[v] = 1
        for ci in hard_pos[v]: hard_sat_count[ci] += 1
        for ci in hard_neg[v]: hard_sat_count[ci] -= 1
        for ci in soft_pos[v]: soft_sat_count[ci] += 1
        for ci in soft_neg[v]: soft_sat_count[ci] -= 1


def compute_flip_delta(v, assignment, soft_pos, soft_neg, soft_sat_count, soft_weights):
    """Compute the change in soft cost if variable v is flipped. Negative = improvement."""
    delta = 0
    if assignment[v]:
        for ci in soft_pos[v]:
            if soft_sat_count[ci] == 1: delta += soft_weights[ci]
        for ci in soft_neg[v]:
            if soft_sat_count[ci] == 0: delta -= soft_weights[ci]
    else:
        for ci in soft_pos[v]:
            if soft_sat_count[ci] == 0: delta -= soft_weights[ci]
        for ci in soft_neg[v]:
            if soft_sat_count[ci] == 1: delta += soft_weights[ci]
    return delta


def flip_preserves_hard(v, assignment, hard_pos, hard_neg, hard_sat_count):
    """Check if flipping v would violate any hard clause."""
    if assignment[v]:
        for ci in hard_pos[v]:
            if hard_sat_count[ci] == 1: return False
    else:
        for ci in hard_neg[v]:
            if hard_sat_count[ci] == 1: return False
    return True


def sat_init(hard_clauses, nvars, solver_name='g4'):
    """Find a feasible assignment satisfying all hard clauses using pysat.

    Args:
        solver_name: 'g4' (glucose4, default) or 'cd19' (CaDiCaL, better for large instances).

    Returns bytearray assignment (1-indexed) or None if UNSAT.
    """
    with Solver(name=solver_name) as sat:
        for clause in hard_clauses:
            sat.add_clause(clause)
        if not sat.solve():
            return None
        model = sat.get_model()

    assignment = bytearray(nvars + 1)
    for lit in model:
        v = abs(lit)
        if v <= nvars:
            assignment[v] = 1 if lit > 0 else 0
    return assignment


def greedy_sat(nvars, hard_clauses, soft_clauses, timeout=240, solver_name='g4'):
    """Greedy SAT with selector variables for instances with few soft clauses.

    Sorts soft clauses by weight (heaviest first) and greedily tries to satisfy each
    using SAT solver with assumptions. Best for nsofts < ~500.

    Args:
        solver_name: pysat solver name. 'g4' (glucose4) is default, 'cd19' (CaDiCaL)
                     is better for instances where glucose4 gets stuck.

    Returns (solution_lits, cost) or (None, None).
    """
    nsoft = len(soft_clauses)
    t0 = time.time()

    with Solver(name=solver_name) as sat:
        for clause in hard_clauses:
            sat.add_clause(clause)

        selectors = []
        for i, (w, clause) in enumerate(soft_clauses):
            sel = nvars + 1 + i
            selectors.append(sel)
            sat.add_clause([-sel] + clause)

        indexed_softs = sorted(range(nsoft), key=lambda i: soft_clauses[i][0], reverse=True)

        must_satisfy = []
        best_cost = sum(w for w, _ in soft_clauses)
        best_model = None

        if sat.solve():
            model = sat.get_model()
            true_lits = set(model)
            cost = sum(w for w, cl in soft_clauses if not any(lit in true_lits for lit in cl))
            if cost < best_cost:
                best_cost = cost
                best_model = model

        for idx in indexed_softs:
            if time.time() - t0 > timeout:
                break
            assumptions = must_satisfy + [selectors[idx]]
            if sat.solve(assumptions=assumptions):
                must_satisfy.append(selectors[idx])
                model = sat.get_model()
                true_lits = set(model)
                cost = sum(w for w, cl in soft_clauses if not any(lit in true_lits for lit in cl))
                if cost < best_cost:
                    best_cost = cost
                    best_model = model

    if best_model is None:
        return None, None

    solution = [lit for lit in best_model if abs(lit) <= nvars]
    present = set(abs(lit) for lit in solution)
    for v in range(1, nvars + 1):
        if v not in present:
            solution.append(v)
    return solution, best_cost


def _update_neighbor_scores(v, assignment, soft_clauses, soft_pos, soft_neg,
                            soft_sat_count, soft_weights, score):
    """Incrementally update score array for neighbors after flipping v.

    Call AFTER flip_variable. Then recompute score[v] separately since neighbor
    updates may touch v's score through shared clauses.
    """
    if assignment[v]:
        # v was just set to 1: soft_pos sat_count increased, soft_neg decreased
        for ci in soft_pos[v]:
            w = soft_weights[ci]
            if soft_sat_count[ci] == 1:
                # 0→1: clause became satisfied, vars that could make it no longer gain
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if (lit > 0 and not assignment[vv]) or (lit < 0 and assignment[vv]):
                        score[vv] += w
            elif soft_sat_count[ci] == 2:
                # 1→2: other satisfier no longer breaks
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if vv != v and ((lit > 0 and assignment[vv]) or (lit < 0 and not assignment[vv])):
                        score[vv] -= w
                        break
        for ci in soft_neg[v]:
            w = soft_weights[ci]
            if soft_sat_count[ci] == 0:
                # 1→0: clause became unsatisfied, vars that could make it now gain
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if (lit > 0 and not assignment[vv]) or (lit < 0 and assignment[vv]):
                        score[vv] -= w
            elif soft_sat_count[ci] == 1:
                # 2→1: remaining satisfier now breaks
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if vv != v and ((lit > 0 and assignment[vv]) or (lit < 0 and not assignment[vv])):
                        score[vv] += w
                        break
    else:
        # v was just set to 0: soft_neg sat_count increased, soft_pos decreased
        for ci in soft_neg[v]:
            w = soft_weights[ci]
            if soft_sat_count[ci] == 1:
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if (lit > 0 and not assignment[vv]) or (lit < 0 and assignment[vv]):
                        score[vv] += w
            elif soft_sat_count[ci] == 2:
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if vv != v and ((lit > 0 and assignment[vv]) or (lit < 0 and not assignment[vv])):
                        score[vv] -= w
                        break
        for ci in soft_pos[v]:
            w = soft_weights[ci]
            if soft_sat_count[ci] == 0:
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if (lit > 0 and not assignment[vv]) or (lit < 0 and assignment[vv]):
                        score[vv] -= w
            elif soft_sat_count[ci] == 1:
                for lit in soft_clauses[ci][1]:
                    vv = abs(lit)
                    if vv != v and ((lit > 0 and assignment[vv]) or (lit < 0 and not assignment[vv])):
                        score[vv] += w
                        break

    # Recompute score[v] from scratch — neighbor updates above may have touched it
    # through shared clauses
    score[v] = compute_flip_delta(v, assignment, soft_pos, soft_neg, soft_sat_count, soft_weights)


def tabu_search(assignment, soft_clauses, hard_clauses, occ_lists, timeout=240,
                candidates=None, restarts=1, perturb_prob=0.2):
    """Tabu search on soft-clause cost, preserving hard clause feasibility.

    Uses incremental score maintenance: O(clause_length) per step for score updates
    instead of O(candidates * clause_length) for full recomputation.

    Args:
        assignment: bytearray, initial feasible assignment (1-indexed)
        soft_clauses: list of (weight, clause)
        hard_clauses: list of clauses
        occ_lists: tuple from build_occurrence_lists
        timeout: seconds
        candidates: list of variables to consider flipping (default: soft_vars)
        restarts: number of trials with random perturbation (default: 1)
        perturb_prob: probability of flipping each candidate on restart (default: 0.2)

    Returns (best_assignment, best_cost).
    """
    hard_pos, hard_neg, soft_pos, soft_neg, soft_weights, soft_vars = occ_lists
    if candidates is None:
        candidates = soft_vars
    nvars = len(assignment) - 1
    nsoft = len(soft_clauses)

    t0 = time.time()
    overall_best_cost = float('inf')
    overall_best_assign = None
    init_assignment = bytearray(assignment)

    for trial in range(restarts):
        if time.time() - t0 > timeout:
            break
        trial_deadline = min(t0 + timeout, time.time() + timeout / max(1, restarts - trial))

        # Reset to initial assignment with perturbation on restarts
        assignment = bytearray(init_assignment)
        if trial > 0:
            for v in candidates:
                if random.random() < perturb_prob:
                    assignment[v] = 1 - assignment[v]
            # Fix hard violations via walksat if we have hard clauses
            if hard_clauses:
                hard_sat_count_tmp = [0] * len(hard_clauses)
                for i, clause in enumerate(hard_clauses):
                    for lit in clause:
                        v = abs(lit)
                        if (lit > 0 and assignment[v]) or (lit < 0 and not assignment[v]):
                            hard_sat_count_tmp[i] += 1
                remaining = walksat_hard(assignment, hard_clauses, hard_pos, hard_neg,
                                         hard_sat_count_tmp)
                if remaining > 0:
                    continue

        hard_sat_count, soft_sat_count = compute_sat_counts(assignment, hard_clauses, soft_clauses)
        current_cost = sum(soft_weights[i] for i in range(nsoft) if soft_sat_count[i] == 0)
        best_cost = current_cost
        best_assign = bytearray(assignment)

        # Initialize scores
        score = [0] * (nvars + 1)
        for v in candidates:
            score[v] = compute_flip_delta(v, assignment, soft_pos, soft_neg, soft_sat_count, soft_weights)

        tabu = {}
        tabu_tenure = max(7, len(candidates) // 5)
        step = 0

        while time.time() < trial_deadline:
            step += 1
            best_v = -1
            best_delta = float('inf')

            for v in candidates:
                if score[v] < best_delta:
                    if v not in tabu or tabu[v] <= step or current_cost + score[v] < best_cost:
                        if flip_preserves_hard(v, assignment, hard_pos, hard_neg, hard_sat_count):
                            best_delta = score[v]
                            best_v = v

            if best_v == -1:
                break

            flip_variable(best_v, assignment, hard_pos, hard_neg, soft_pos, soft_neg,
                          hard_sat_count, soft_sat_count)
            current_cost += best_delta
            _update_neighbor_scores(best_v, assignment, soft_clauses, soft_pos, soft_neg,
                                    soft_sat_count, soft_weights, score)
            tabu[best_v] = step + tabu_tenure + random.randint(0, 3)

            if current_cost < best_cost:
                best_cost = current_cost
                best_assign = bytearray(assignment)

        if best_cost < overall_best_cost:
            overall_best_cost = best_cost
            overall_best_assign = bytearray(best_assign)

    return overall_best_assign, overall_best_cost


def walksat_hard(assignment, hard_clauses, hard_pos, hard_neg, hard_sat_count,
                 max_iters=500000, noise_prob=0.4):
    """WalkSAT to fix hard clause violations in place.

    Modifies assignment and hard_sat_count. Returns number of remaining violations.
    """
    nhard = len(hard_clauses)
    for _ in range(max_iters):
        unsat = [i for i in range(nhard) if hard_sat_count[i] == 0]
        if not unsat:
            return 0
        ci = random.choice(unsat)
        lit = random.choice(hard_clauses[ci])
        v = abs(lit)
        if assignment[v]:
            assignment[v] = 0
            for j in hard_pos[v]: hard_sat_count[j] -= 1
            for j in hard_neg[v]: hard_sat_count[j] += 1
        else:
            assignment[v] = 1
            for j in hard_pos[v]: hard_sat_count[j] += 1
            for j in hard_neg[v]: hard_sat_count[j] -= 1
    return sum(1 for i in range(nhard) if hard_sat_count[i] == 0)


def walksat_soft(assignment, soft_clauses, hard_clauses, occ_lists, timeout=120):
    """WalkSAT-based soft clause optimizer for instances with many hard + soft clauses.

    Instead of scanning all candidates (slow with large occurrence lists), picks random
    unsatisfied soft clauses and tries to flip variables to satisfy them.
    O(clause_length) per step instead of O(candidates * occurrence_length).

    Best for: instances with many soft clauses AND many hard clauses (e.g. ParametricRBAC domino).
    """
    hard_pos, hard_neg, soft_pos, soft_neg, soft_weights, soft_vars = occ_lists
    hard_sat_count, soft_sat_count = compute_sat_counts(assignment, hard_clauses, soft_clauses)
    nsoft = len(soft_clauses)

    current_cost = sum(soft_weights[i] for i in range(nsoft) if soft_sat_count[i] == 0)
    best_cost = current_cost
    best_assign = bytearray(assignment)

    deadline = time.time() + timeout
    while time.time() < deadline:
        unsat_softs = [i for i in range(nsoft) if soft_sat_count[i] == 0]
        if not unsat_softs:
            break
        ci = random.choice(unsat_softs)
        w, clause = soft_clauses[ci]
        random.shuffle(clause)
        for lit in clause:
            v = abs(lit)
            would_satisfy = (lit > 0 and not assignment[v]) or (lit < 0 and assignment[v])
            if not would_satisfy:
                continue
            if flip_preserves_hard(v, assignment, hard_pos, hard_neg, hard_sat_count):
                flip_variable(v, assignment, hard_pos, hard_neg, soft_pos, soft_neg,
                            hard_sat_count, soft_sat_count)
                current_cost = sum(soft_weights[j] for j in range(nsoft) if soft_sat_count[j] == 0)
                if current_cost < best_cost:
                    best_cost = current_cost
                    best_assign = bytearray(assignment)
                break

    return best_assign, best_cost


def multi_init(nvars, hard_clauses, soft_clauses, timeout=60, max_trials=500,
               solvers=None):
    """Multi-init: run multiple SAT solvers with random assumptions to find diverse solutions.

    Key discovery: different solvers and random variable assumptions produce wildly different
    feasible assignments with very different soft costs. Extremely effective for breaking out
    of single-solver local optima.

    Does NOT help for judgment-aggregation (all SAT baselines are terrible for those).

    Args:
        nvars: number of variables
        hard_clauses: list of clauses
        soft_clauses: list of (weight, clause)
        timeout: seconds
        max_trials: max number of solver invocations
        solvers: list of pysat solver names (default: ['cd19', 'g4', 'cd15', 'mc'] for small,
                 ['cd19', 'cd15'] for large)

    Returns (best_solution_lits, best_cost) or (None, None).
    """
    if solvers is None:
        solvers = ['cd19', 'g4', 'cd15', 'mc'] if nvars < 300000 else ['cd19', 'cd15']

    best_cost = float('inf')
    best_sol = None
    t0 = time.time()

    for trial in range(max_trials):
        if time.time() - t0 > timeout:
            break
        sn = random.choice(solvers)
        try:
            with Solver(name=sn) as sat:
                for clause in hard_clauses:
                    sat.add_clause(clause)
                n_assumptions = max(1, min(int(nvars * random.uniform(0.005, 0.3)), 300))
                assumptions = [random.choice([v, -v]) for v in random.sample(range(1, nvars + 1), n_assumptions)]
                if sat.solve(assumptions=assumptions):
                    model = sat.get_model()
                elif sat.solve():
                    model = sat.get_model()
                else:
                    continue
                solution = [lit for lit in model if abs(lit) <= nvars]
                present = set(abs(lit) for lit in solution)
                for v in range(1, nvars + 1):
                    if v not in present:
                        solution.append(v)
                from library.wcnf_parser import evaluate_cost
                cost = evaluate_cost(solution, soft_clauses)
                if cost < best_cost:
                    best_cost = cost
                    best_sol = solution
        except Exception:
            pass

    return best_sol, best_cost


def randomized_greedy(nvars, hard_clauses, soft_clauses, timeout=240, n_trials=100,
                      solvers=None):
    """Randomized greedy SAT: multiple random orderings of soft clause priorities.

    Key discovery: standard greedy SAT sorts by weight descending, but random orderings
    can find dramatically better solutions by exploring different corners of solution space.

    Game-changer results:
    - causal_n7: 94.9B→37.5B (2.53x→optimal, matching reference)
    - timetabling comp21: 168→120 (2.27x→1.62x)

    Most effective for instances with <5000 soft clauses where each greedy iteration is fast.

    Args:
        nvars: number of variables
        hard_clauses: list of clauses
        soft_clauses: list of (weight, clause)
        timeout: seconds
        n_trials: max number of random orderings to try
        solvers: list of pysat solver names (default: ['cd19', 'g4'])

    Returns (best_solution_lits, best_cost) or (None, None).
    """
    if solvers is None:
        solvers = ['cd19', 'g4']

    from library.wcnf_parser import evaluate_cost

    nsoft = len(soft_clauses)
    best_cost = float('inf')
    best_sol = None
    t0 = time.time()

    for trial in range(n_trials):
        if time.time() - t0 > timeout:
            break

        sn = random.choice(solvers)
        try:
            with Solver(name=sn) as sat:
                for clause in hard_clauses:
                    sat.add_clause(clause)

                selectors = []
                for i, (w, clause) in enumerate(soft_clauses):
                    sel = nvars + 1 + i
                    selectors.append(sel)
                    sat.add_clause([-sel] + clause)

                order = list(range(nsoft))
                random.shuffle(order)

                must_satisfy = []
                for idx in order:
                    if time.time() - t0 > timeout:
                        break
                    assumptions = must_satisfy + [selectors[idx]]
                    if sat.solve(assumptions=assumptions):
                        must_satisfy.append(selectors[idx])

                if sat.solve(assumptions=must_satisfy):
                    model = sat.get_model()
                    solution = [lit for lit in model if abs(lit) <= nvars]
                    present = set(abs(lit) for lit in solution)
                    for v in range(1, nvars + 1):
                        if v not in present:
                            solution.append(v)
                    cost = evaluate_cost(solution, soft_clauses)
                    if cost < best_cost:
                        best_cost = cost
                        best_sol = solution
        except Exception:
            pass

    return best_sol, best_cost


def assignment_to_lits(assignment):
    """Convert bytearray assignment to signed literal list."""
    return [v if assignment[v] else -v for v in range(1, len(assignment))]


def simulated_annealing(assignment, soft_clauses, hard_clauses, occ_lists, timeout=240,
                        t_start=1.0, t_end=0.001, candidates=None):
    """Simulated annealing on soft-clause cost, preserving hard clause feasibility.

    Accepts worse solutions with probability exp(-delta/temperature), where temperature
    decreases exponentially from t_start to t_end. This can escape local optima that
    tabu search cannot.

    Returns (best_assignment, best_cost).
    """
    import math
    hard_pos, hard_neg, soft_pos, soft_neg, soft_weights, soft_vars = occ_lists
    if candidates is None:
        candidates = soft_vars
    nvars = len(assignment) - 1
    nsoft = len(soft_clauses)

    hard_sat_count, soft_sat_count = compute_sat_counts(assignment, hard_clauses, soft_clauses)
    current_cost = sum(soft_weights[i] for i in range(nsoft) if soft_sat_count[i] == 0)
    best_cost = current_cost
    best_assign = bytearray(assignment)

    t0 = time.time()
    step = 0
    ncands = len(candidates)

    while True:
        elapsed = time.time() - t0
        if elapsed >= timeout:
            break

        # Temperature schedule
        frac = elapsed / timeout
        temp = t_start * ((t_end / t_start) ** frac)

        # Pick random candidate
        v = candidates[random.randint(0, ncands - 1)]

        if not flip_preserves_hard(v, assignment, hard_pos, hard_neg, hard_sat_count):
            continue

        delta = compute_flip_delta(v, assignment, soft_pos, soft_neg, soft_sat_count, soft_weights)

        # Accept improving moves always, worse moves probabilistically
        exponent = -delta / (temp * best_cost + 1e-10)
        if delta <= 0 or (exponent > -500 and random.random() < math.exp(max(-500, min(500, exponent)))):
            flip_variable(v, assignment, hard_pos, hard_neg, soft_pos, soft_neg,
                          hard_sat_count, soft_sat_count)
            current_cost += delta
            step += 1

            if current_cost < best_cost:
                best_cost = current_cost
                best_assign = bytearray(assignment)

    return best_assign, best_cost
