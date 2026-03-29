"""Small local harness for iterating on the standalone solver core."""

import argparse
import os
import sys
import time

SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

import rubiks_solver_core as solver_core
from rubiks_move_notation import generate_scramble_moves, parse_move_sequence


def main():
    parser = argparse.ArgumentParser(description="Run the standalone Rubik's Cube solver core.")
    parser.add_argument("moves", nargs="*", help="Optional scramble moves such as R U R' U'")
    parser.add_argument("--algorithm", help="Optional quoted algorithm text such as \"R U R' U'\"")
    parser.add_argument("--length", type=int, default=20, help="Random scramble length when no moves are provided")
    parser.add_argument("--nodes", type=int, default=solver_core.SOLVER_MAX_NODES, help="Maximum search nodes")
    parser.add_argument("--seconds", type=float, default=solver_core.SOLVER_MAX_SECONDS, help="Maximum search seconds")
    parser.add_argument("--phase1", type=int, default=solver_core.TWO_PHASE_MAX_PHASE1_DEPTH, help="Phase-1 depth limit")
    parser.add_argument("--phase2", type=int, default=solver_core.TWO_PHASE_MAX_PHASE2_DEPTH, help="Phase-2 depth limit")
    args = parser.parse_args()

    solver_core.configure(
        max_nodes=args.nodes,
        max_seconds=args.seconds,
        phase1_depth=args.phase1,
        phase2_depth=args.phase2,
    )
    solver_core.set_callbacks(log_callback=print)

    if args.algorithm:
        scramble_moves = parse_move_sequence(args.algorithm, valid_moves=solver_core.MOVES)
    elif args.moves:
        scramble_moves = parse_move_sequence(" ".join(args.moves), valid_moves=solver_core.MOVES)
    else:
        quarter_turns = [move_name for move_name in solver_core.MOVES if "2" not in move_name]
        scramble_moves = generate_scramble_moves(args.length, move_names=quarter_turns)
    start_state = solver_core.apply_move_sequence_to_cube_state(
        solver_core.get_canonical_solved_state(),
        scramble_moves,
    )

    print("Scramble: {0}".format(" ".join(scramble_moves)))
    # Use wall-clock timing in the harness so local benchmarking reflects the
    # real elapsed time you would observe from the command line.
    solve_start = time.perf_counter()
    solution_moves, stats = solver_core.find_two_phase_solution(
        start_state,
        max_nodes=args.nodes,
    )
    elapsed = time.perf_counter() - solve_start

    if solution_moves is None:
        print("No solution found")
        print(stats)
        print("Elapsed: {0:.2f}s".format(elapsed))
        return

    quarter_turns = solver_core.expand_solver_moves(solution_moves)
    print("Solution: {0}".format(" ".join(solution_moves)))
    print("Quarter turns: {0}".format(" ".join(quarter_turns)))
    print(stats)
    print("Elapsed: {0:.2f}s".format(elapsed))


if __name__ == "__main__":
    main()
