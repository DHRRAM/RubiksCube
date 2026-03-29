import unittest

import rubiks_solver_core as solver_core
from rubiks_move_notation import (
    MOVE_EXPANSIONS,
    SEARCH_MOVE_NAMES,
    invert_move_sequence,
)
from rubiks_state_utils import (
    apply_move_sequence_to_cube_state,
    apply_move_to_cube_state,
    build_move_state_tables,
    build_piece_state_catalogs,
    build_solved_cube_state,
    build_solved_piece_states,
    build_solver_piece_metadata,
)


def build_shared_state_fixture():
    solver_pieces = build_solver_piece_metadata()
    solved_piece_states = build_solved_piece_states(solver_pieces)
    (
        piece_state_infos,
        piece_state_to_code,
        _position_ids,
        _orientation_ids,
    ) = build_piece_state_catalogs(solved_piece_states)
    move_state_tables = build_move_state_tables(piece_state_infos, piece_state_to_code)
    solved_cube_state = build_solved_cube_state(piece_state_to_code, solved_piece_states)
    return {
        "solver_pieces": solver_pieces,
        "solved_piece_states": solved_piece_states,
        "move_state_tables": move_state_tables,
        "solved_cube_state": solved_cube_state,
    }


class MoveMappingAndStateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = build_shared_state_fixture()
        solver_core.ensure_solver_state()

    def test_search_move_expansions_match_direct_state_tables(self):
        solved_state = self.shared["solved_cube_state"]
        move_state_tables = self.shared["move_state_tables"]

        for move_name in SEARCH_MOVE_NAMES:
            with self.subTest(move=move_name):
                direct_state = apply_move_to_cube_state(
                    solved_state,
                    move_state_tables,
                    move_name,
                )
                expanded_state = apply_move_sequence_to_cube_state(
                    solved_state,
                    move_state_tables,
                    MOVE_EXPANSIONS[move_name],
                )
                self.assertEqual(direct_state, expanded_state)

    def test_each_search_move_round_trips_with_its_inverse(self):
        solved_state = self.shared["solved_cube_state"]
        move_state_tables = self.shared["move_state_tables"]

        for move_name in SEARCH_MOVE_NAMES:
            with self.subTest(move=move_name):
                round_trip_state = apply_move_sequence_to_cube_state(
                    solved_state,
                    move_state_tables,
                    [move_name] + invert_move_sequence([move_name]),
                )
                self.assertEqual(round_trip_state, solved_state)

    def test_solver_core_stays_compatible_with_shared_state_encoding(self):
        shared_solved_state = self.shared["solved_cube_state"]
        shared_move_state_tables = self.shared["move_state_tables"]
        solver_solved_state = solver_core.get_canonical_solved_state()

        self.assertEqual(shared_solved_state, solver_solved_state)
        self.assertEqual(self.shared["solver_pieces"], solver_core.SOLVER_PIECES)
        self.assertEqual(self.shared["solved_piece_states"], solver_core.SOLVED_PIECE_STATES)

        for move_name in SEARCH_MOVE_NAMES:
            with self.subTest(move=move_name):
                shared_next_state = apply_move_to_cube_state(
                    shared_solved_state,
                    shared_move_state_tables,
                    move_name,
                )
                solver_next_state = solver_core.apply_move_to_cube_state(
                    solver_solved_state,
                    move_name,
                )
                self.assertEqual(shared_next_state, solver_next_state)


if __name__ == "__main__":
    unittest.main()
