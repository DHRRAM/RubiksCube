"""Standalone pure-Python solver core for the Maya Rubik's Cube tool.

This module mirrors the logical state encoding used in `rubiks_cube.py` so the
Maya tool can hand its current cube state directly to the solver.
"""

import math
import os
import pickle
import time
from array import array
from itertools import combinations

from rubiks_move_notation import (
    MOVES,
    PHASE1_MOVE_NAMES,
    PHASE2_MOVE_NAMES,
    SEARCH_MOVE_NAMES,
    expand_solver_moves,
    get_inverse_move,
    get_move_face,
    should_prune_search_move,
)
from rubiks_state_utils import (
    apply_move_sequence_to_cube_state as apply_move_sequence_with_tables,
    apply_move_to_cube_state as apply_move_to_cube_state_with_tables,
    apply_move_to_piece_state,
    build_move_state_tables as build_shared_move_state_tables,
    build_piece_state_catalogs as build_shared_piece_state_catalogs,
    build_solved_cube_state as build_shared_solved_cube_state,
    build_solved_piece_states as build_shared_solved_piece_states,
    build_solver_piece_metadata as build_shared_solver_piece_metadata,
    rotate_logical_vector,
)


SOLVER_MAX_DEPTH = 14
SOLVER_MAX_NODES = 2000000
SOLVER_MAX_SECONDS = 20.0
SOLVER_PROGRESS_STEP = 5000
SOLVER_TIME_SOURCE_LABEL = "CPU"
TWO_PHASE_MAX_PHASE1_DEPTH = 12
TWO_PHASE_MAX_PHASE2_DEPTH = 24

LOG_CALLBACK = None
PROGRESS_CALLBACK = None

SOLVER_PIECES = ()
SOLVED_PIECE_STATES = ()
PIECE_STATE_INFOS = ()
PIECE_STATE_TO_CODE = ()
PIECE_POSITION_IDS = ()
PIECE_ORIENTATION_IDS = ()
MOVE_STATE_TABLES = {}
CANONICAL_SOLVED_CUBE_STATE = ()
SOLVED_CUBE_STATE = ()
CUBE_STATE = ()
CORNER_PIECE_INDICES = ()
EDGE_PIECE_INDICES = ()
CORNER_POSITION_INDEX_BY_VECTOR = {}
EDGE_POSITION_INDEX_BY_VECTOR = {}
CORNER_STATE_CODE_LOOKUPS = ()
EDGE_STATE_CODE_LOOKUPS = ()
EDGE_ORIENTATION_VALUES = ()
PHASE1_CORNER_ORIENTATION_MOVE_TABLE = array("H")
PHASE1_EDGE_ORIENTATION_MOVE_TABLE = array("H")
PHASE1_SLICE_POSITION_MOVE_TABLE = array("H")
PHASE2_CORNER_PERMUTATION_MOVE_TABLE = array("H")
PHASE2_EDGE_PERMUTATION_MOVE_TABLE = array("H")
PHASE2_SLICE_PERMUTATION_MOVE_TABLE = array("H")
PHASE1_CORNER_EDGE_PRUNING_TABLE = bytearray()
PHASE1_CORNER_SLICE_PRUNING_TABLE = bytearray()
PHASE1_EDGE_SLICE_PRUNING_TABLE = bytearray()
PHASE2_CORNER_SLICE_PRUNING_TABLE = bytearray()
PHASE2_EDGE_SLICE_PRUNING_TABLE = bytearray()
PHASE1_REVERSE_FRONTIER_KEYS = array("I")
PHASE1_REVERSE_FRONTIER_DEPTHS = bytearray()
PHASE1_REVERSE_FRONTIER_MASK = 0
PHASE1_REVERSE_FRONTIER_ENTRY_COUNT = 0
PHASE1_MOVE_COUNT = len(PHASE1_MOVE_NAMES)
PHASE2_MOVE_COUNT = len(PHASE2_MOVE_NAMES)
CORNER_ORIENTATION_COUNT = 3 ** 7
EDGE_ORIENTATION_COUNT = 2 ** 11
CORNER_PERMUTATION_COUNT = math.factorial(8)
EDGE_PERMUTATION_COUNT = math.factorial(8)
SLICE_PERMUTATION_COUNT = math.factorial(4)
SLICE_POSITION_COMBINATIONS = tuple(combinations(range(12), 4))
SLICE_POSITION_COUNT = len(SLICE_POSITION_COMBINATIONS)
SLICE_POSITION_TO_INDEX = {
    combination: index
    for index, combination in enumerate(SLICE_POSITION_COMBINATIONS)
}
SLICE_EDGE_PIECES = (4, 5, 6, 7)
SLICE_EDGE_PIECE_SET = set(SLICE_EDGE_PIECES)
SLICE_EDGE_POSITIONS = (4, 5, 6, 7)
SOLVED_SLICE_POSITION_INDEX = SLICE_POSITION_TO_INDEX[tuple(sorted(SLICE_EDGE_POSITIONS))]
SLICE_EDGE_POSITION_TO_INDEX = {
    position: index
    for index, position in enumerate(SLICE_EDGE_POSITIONS)
}
NON_SLICE_EDGE_PIECES = (0, 1, 2, 3, 8, 9, 10, 11)
PHASE2_UD_EDGE_POSITIONS = (0, 1, 2, 3, 8, 9, 10, 11)
PHASE2_UD_EDGE_POSITION_TO_INDEX = {
    position: index
    for index, position in enumerate(PHASE2_UD_EDGE_POSITIONS)
}
PHASE2_UD_EDGE_PIECES = NON_SLICE_EDGE_PIECES
PHASE2_UD_EDGE_PIECE_TO_INDEX = {
    piece_id: index
    for index, piece_id in enumerate(PHASE2_UD_EDGE_PIECES)
}
SOLVED_CORNER_PERMUTATION = tuple(range(8))
SOLVED_CORNER_ORIENTATION = (0,) * 8
SOLVED_EDGE_PERMUTATION = tuple(range(12))
SOLVED_EDGE_ORIENTATION = (0,) * 12
PRUNING_TABLE_UNVISITED = 255
PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT = 7
PHASE1_REVERSE_FRONTIER_EMPTY_KEY = 0xFFFFFFFF
PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH = 255
PHASE1_REVERSE_FRONTIER_HASH_MULTIPLIER = 2654435761
PHASE1_REVERSE_FRONTIER_SLOT_COUNT_BY_DEPTH = {
    6: 1 << 21,
    7: 1 << 24,
}
CACHE_VERSION = 2
FACTORIALS = (
    1,
    1,
    2,
    6,
    24,
    120,
    720,
    5040,
    40320,
)


def configure(
    max_depth=None,
    max_nodes=None,
    max_seconds=None,
    progress_step=None,
    phase1_depth=None,
    phase2_depth=None,
):
    global SOLVER_MAX_DEPTH
    global SOLVER_MAX_NODES
    global SOLVER_MAX_SECONDS
    global SOLVER_PROGRESS_STEP
    global TWO_PHASE_MAX_PHASE1_DEPTH
    global TWO_PHASE_MAX_PHASE2_DEPTH

    if max_depth is not None:
        SOLVER_MAX_DEPTH = max_depth
    if max_nodes is not None:
        SOLVER_MAX_NODES = max_nodes
    if max_seconds is not None:
        SOLVER_MAX_SECONDS = max_seconds
    if progress_step is not None:
        SOLVER_PROGRESS_STEP = progress_step
    if phase1_depth is not None:
        TWO_PHASE_MAX_PHASE1_DEPTH = phase1_depth
    if phase2_depth is not None:
        TWO_PHASE_MAX_PHASE2_DEPTH = phase2_depth


def set_callbacks(log_callback=None, progress_callback=None):
    global LOG_CALLBACK
    global PROGRESS_CALLBACK

    LOG_CALLBACK = log_callback
    PROGRESS_CALLBACK = progress_callback


def log(message):
    if LOG_CALLBACK is not None:
        LOG_CALLBACK(message)
        return
    print(message)


def process_events():
    if PROGRESS_CALLBACK is not None:
        PROGRESS_CALLBACK()


def get_solver_clock_time():
    # Measure solve budgets in CPU time so background-window throttling does
    # not consume the search budget while the solver is effectively paused.
    clock = getattr(time, "thread_time", None)
    if clock is not None:
        return clock()
    return time.process_time()


def get_solver_cache_path():
    # Cache the heavy move/pruning/frontier tables beside the module so the
    # expensive first build can be reused across fresh Maya/Python sessions.
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "rubiks_solver_core_cache_v{0}.pkl".format(CACHE_VERSION),
    )


def load_two_phase_solver_cache():
    global PHASE1_CORNER_ORIENTATION_MOVE_TABLE
    global PHASE1_EDGE_ORIENTATION_MOVE_TABLE
    global PHASE1_SLICE_POSITION_MOVE_TABLE
    global PHASE2_CORNER_PERMUTATION_MOVE_TABLE
    global PHASE2_EDGE_PERMUTATION_MOVE_TABLE
    global PHASE2_SLICE_PERMUTATION_MOVE_TABLE
    global PHASE1_CORNER_EDGE_PRUNING_TABLE
    global PHASE1_CORNER_SLICE_PRUNING_TABLE
    global PHASE1_EDGE_SLICE_PRUNING_TABLE
    global PHASE2_CORNER_SLICE_PRUNING_TABLE
    global PHASE2_EDGE_SLICE_PRUNING_TABLE
    global PHASE1_REVERSE_FRONTIER_KEYS
    global PHASE1_REVERSE_FRONTIER_DEPTHS
    global PHASE1_REVERSE_FRONTIER_MASK
    global PHASE1_REVERSE_FRONTIER_ENTRY_COUNT

    cache_path = get_solver_cache_path()
    if not os.path.exists(cache_path):
        return False

    try:
        # The cached reverse frontier is deliberately packed into arrays/bytes
        # so loading it is much cheaper than rebuilding it from scratch.
        log("Loading two-phase solver cache from disk...")
        with open(cache_path, "rb") as cache_file:
            cache_data = pickle.load(cache_file)
    except Exception as error:
        log("Ignoring stale two-phase solver cache ({0})".format(error))
        return False

    PHASE1_CORNER_ORIENTATION_MOVE_TABLE = cache_data["phase1_corner_orientation_move_table"]
    PHASE1_EDGE_ORIENTATION_MOVE_TABLE = cache_data["phase1_edge_orientation_move_table"]
    PHASE1_SLICE_POSITION_MOVE_TABLE = cache_data["phase1_slice_position_move_table"]
    PHASE2_CORNER_PERMUTATION_MOVE_TABLE = cache_data["phase2_corner_permutation_move_table"]
    PHASE2_EDGE_PERMUTATION_MOVE_TABLE = cache_data["phase2_edge_permutation_move_table"]
    PHASE2_SLICE_PERMUTATION_MOVE_TABLE = cache_data["phase2_slice_permutation_move_table"]
    PHASE1_CORNER_EDGE_PRUNING_TABLE = cache_data["phase1_corner_edge_pruning_table"]
    PHASE1_CORNER_SLICE_PRUNING_TABLE = cache_data["phase1_corner_slice_pruning_table"]
    PHASE1_EDGE_SLICE_PRUNING_TABLE = cache_data["phase1_edge_slice_pruning_table"]
    PHASE2_CORNER_SLICE_PRUNING_TABLE = cache_data["phase2_corner_slice_pruning_table"]
    PHASE2_EDGE_SLICE_PRUNING_TABLE = cache_data["phase2_edge_slice_pruning_table"]
    PHASE1_REVERSE_FRONTIER_KEYS = cache_data["phase1_reverse_frontier_keys"]
    PHASE1_REVERSE_FRONTIER_DEPTHS = cache_data["phase1_reverse_frontier_depths"]
    PHASE1_REVERSE_FRONTIER_MASK = cache_data["phase1_reverse_frontier_mask"]
    PHASE1_REVERSE_FRONTIER_ENTRY_COUNT = cache_data["phase1_reverse_frontier_entry_count"]
    return True


def save_two_phase_solver_cache():
    cache_path = get_solver_cache_path()
    cache_data = {
        "phase1_corner_orientation_move_table": PHASE1_CORNER_ORIENTATION_MOVE_TABLE,
        "phase1_edge_orientation_move_table": PHASE1_EDGE_ORIENTATION_MOVE_TABLE,
        "phase1_slice_position_move_table": PHASE1_SLICE_POSITION_MOVE_TABLE,
        "phase2_corner_permutation_move_table": PHASE2_CORNER_PERMUTATION_MOVE_TABLE,
        "phase2_edge_permutation_move_table": PHASE2_EDGE_PERMUTATION_MOVE_TABLE,
        "phase2_slice_permutation_move_table": PHASE2_SLICE_PERMUTATION_MOVE_TABLE,
        "phase1_corner_edge_pruning_table": PHASE1_CORNER_EDGE_PRUNING_TABLE,
        "phase1_corner_slice_pruning_table": PHASE1_CORNER_SLICE_PRUNING_TABLE,
        "phase1_edge_slice_pruning_table": PHASE1_EDGE_SLICE_PRUNING_TABLE,
        "phase2_corner_slice_pruning_table": PHASE2_CORNER_SLICE_PRUNING_TABLE,
        "phase2_edge_slice_pruning_table": PHASE2_EDGE_SLICE_PRUNING_TABLE,
        "phase1_reverse_frontier_keys": PHASE1_REVERSE_FRONTIER_KEYS,
        "phase1_reverse_frontier_depths": PHASE1_REVERSE_FRONTIER_DEPTHS,
        "phase1_reverse_frontier_mask": PHASE1_REVERSE_FRONTIER_MASK,
        "phase1_reverse_frontier_entry_count": PHASE1_REVERSE_FRONTIER_ENTRY_COUNT,
    }
    try:
        with open(cache_path, "wb") as cache_file:
            pickle.dump(cache_data, cache_file, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as error:
        log("Skipping two-phase solver cache save ({0})".format(error))


INVERSE_MOVES = {
    move_name: get_inverse_move(move_name)
    for move_name in SEARCH_MOVE_NAMES
}


def build_solver_piece_metadata():
    return build_shared_solver_piece_metadata()


def build_solved_piece_states():
    return build_shared_solved_piece_states(SOLVER_PIECES)


def build_piece_state_catalogs():
    return build_shared_piece_state_catalogs(SOLVED_PIECE_STATES)


def build_move_state_tables():
    return build_shared_move_state_tables(PIECE_STATE_INFOS, PIECE_STATE_TO_CODE)


def build_solved_cube_state():
    return build_shared_solved_cube_state(PIECE_STATE_TO_CODE, SOLVED_PIECE_STATES)


def get_vector_axis_index(vector):
    if vector[0] != 0:
        return 0
    if vector[1] != 0:
        return 1
    return 2


def get_corner_orientation_from_piece_state(piece_state):
    position = piece_state[0]
    ud_direction = piece_state[1]
    if get_vector_axis_index(ud_direction) == 1:
        return 0

    if position[0] * position[1] * position[2] == 1:
        return 1 if ud_direction == (position[0], 0, 0) else 2

    return 1 if ud_direction == (0, 0, position[2]) else 2


def is_edge_flipping_move(move_name):
    return move_name[0] in ("F", "B") and not move_name.endswith("2")


def rank_permutation(permutation):
    rank = 0
    length = len(permutation)

    for index, value in enumerate(permutation):
        smaller_values = 0
        for later_index in range(index + 1, length):
            if permutation[later_index] < value:
                smaller_values += 1
        rank += smaller_values * FACTORIALS[length - index - 1]

    return rank


def unrank_permutation(rank, length):
    remaining_values = list(range(length))
    permutation = []

    for remaining_count in range(length, 0, -1):
        factorial = FACTORIALS[remaining_count - 1]
        value_index = rank // factorial
        rank %= factorial
        permutation.append(remaining_values.pop(value_index))

    return tuple(permutation)


def encode_corner_orientation(corner_orientation):
    index = 0
    for orientation in corner_orientation[:7]:
        index = index * 3 + orientation
    return index


def decode_corner_orientation(index):
    corner_orientation = [0] * 8
    orientation_sum = 0

    for position_index in range(6, -1, -1):
        orientation = index % 3
        corner_orientation[position_index] = orientation
        orientation_sum += orientation
        index //= 3

    corner_orientation[7] = (-orientation_sum) % 3
    return tuple(corner_orientation)


def encode_edge_orientation(edge_orientation):
    index = 0
    for orientation in edge_orientation[:11]:
        index = (index << 1) | orientation
    return index


def decode_edge_orientation(index):
    edge_orientation = [0] * 12
    orientation_sum = 0

    for position_index in range(10, -1, -1):
        orientation = index & 1
        edge_orientation[position_index] = orientation
        orientation_sum += orientation
        index >>= 1

    edge_orientation[11] = orientation_sum % 2
    return tuple(edge_orientation)


def build_two_phase_solver_metadata():
    global CORNER_PIECE_INDICES
    global EDGE_PIECE_INDICES
    global CORNER_POSITION_INDEX_BY_VECTOR
    global EDGE_POSITION_INDEX_BY_VECTOR
    global CORNER_STATE_CODE_LOOKUPS
    global EDGE_STATE_CODE_LOOKUPS
    global EDGE_ORIENTATION_VALUES

    if (
        CORNER_PIECE_INDICES
        and EDGE_PIECE_INDICES
        and CORNER_STATE_CODE_LOOKUPS
        and EDGE_STATE_CODE_LOOKUPS
        and EDGE_ORIENTATION_VALUES
    ):
        return

    corner_piece_indices = []
    edge_piece_indices = []
    corner_position_index_by_vector = {}
    edge_position_index_by_vector = {}

    for piece_index, piece in enumerate(SOLVER_PIECES):
        if piece["piece_type"] == "corner":
            corner_position_index_by_vector[piece["home_position"]] = len(corner_piece_indices)
            corner_piece_indices.append(piece_index)
        else:
            edge_position_index_by_vector[piece["home_position"]] = len(edge_piece_indices)
            edge_piece_indices.append(piece_index)

    corner_state_code_lookups = []
    for piece_index in corner_piece_indices:
        position_lookup = [[None] * 3 for _unused in range(len(corner_piece_indices))]
        for state_code, piece_state in enumerate(PIECE_STATE_INFOS[piece_index]):
            position_index = corner_position_index_by_vector[piece_state[0]]
            orientation = get_corner_orientation_from_piece_state(piece_state)
            position_lookup[position_index][orientation] = state_code

        corner_state_code_lookups.append(
            tuple(tuple(state_codes) for state_codes in position_lookup)
        )
        if any(state_code is None for state_codes in position_lookup for state_code in state_codes):
            raise ValueError(
                "Incomplete corner state lookup for piece {0}".format(piece_index)
            )

    edge_state_code_lookups = []
    edge_orientation_values = []
    for piece_index in edge_piece_indices:
        position_lookup = [[None] * 2 for _unused in range(len(edge_piece_indices))]
        state_orientation_values = [None] * len(PIECE_STATE_INFOS[piece_index])
        solved_state_code = PIECE_STATE_TO_CODE[piece_index][SOLVED_PIECE_STATES[piece_index]]
        state_orientation_values[solved_state_code] = 0
        queue = [solved_state_code]
        queue_index = 0

        while queue_index < len(queue):
            state_code = queue[queue_index]
            queue_index += 1
            current_orientation = state_orientation_values[state_code]

            for move_name in SEARCH_MOVE_NAMES:
                next_state_code = MOVE_STATE_TABLES[move_name][piece_index][state_code]
                next_orientation = current_orientation
                if next_state_code != state_code and is_edge_flipping_move(move_name):
                    next_orientation = 1 - current_orientation

                previous_orientation = state_orientation_values[next_state_code]
                if previous_orientation is None:
                    state_orientation_values[next_state_code] = next_orientation
                    queue.append(next_state_code)
                    continue

                if previous_orientation != next_orientation:
                    raise ValueError(
                        "Inconsistent edge orientation assignment for piece {0}".format(
                            piece_index
                        )
                    )

        for state_code, piece_state in enumerate(PIECE_STATE_INFOS[piece_index]):
            position_index = edge_position_index_by_vector[piece_state[0]]
            orientation = state_orientation_values[state_code]
            position_lookup[position_index][orientation] = state_code

        edge_state_code_lookups.append(
            tuple(tuple(state_codes) for state_codes in position_lookup)
        )
        if any(state_code is None for state_codes in position_lookup for state_code in state_codes):
            raise ValueError(
                "Incomplete edge state lookup for piece {0}".format(piece_index)
            )
        edge_orientation_values.append(tuple(state_orientation_values))

    CORNER_PIECE_INDICES = tuple(corner_piece_indices)
    EDGE_PIECE_INDICES = tuple(edge_piece_indices)
    CORNER_POSITION_INDEX_BY_VECTOR = corner_position_index_by_vector
    EDGE_POSITION_INDEX_BY_VECTOR = edge_position_index_by_vector
    CORNER_STATE_CODE_LOOKUPS = tuple(corner_state_code_lookups)
    EDGE_STATE_CODE_LOOKUPS = tuple(edge_state_code_lookups)
    EDGE_ORIENTATION_VALUES = tuple(edge_orientation_values)


def build_cube_state_from_coordinates(
    corner_permutation=None,
    corner_orientation=None,
    edge_permutation=None,
    edge_orientation=None,
):
    if corner_permutation is None:
        corner_permutation = SOLVED_CORNER_PERMUTATION
    if corner_orientation is None:
        corner_orientation = SOLVED_CORNER_ORIENTATION
    if edge_permutation is None:
        edge_permutation = SOLVED_EDGE_PERMUTATION
    if edge_orientation is None:
        edge_orientation = SOLVED_EDGE_ORIENTATION

    cube_state = [0] * len(SOLVER_PIECES)
    for position_index, piece_id in enumerate(corner_permutation):
        piece_index = CORNER_PIECE_INDICES[piece_id]
        state_code = CORNER_STATE_CODE_LOOKUPS[piece_id][position_index][corner_orientation[position_index]]
        if state_code is None:
            raise ValueError(
                "Missing corner state for piece {0} at position {1} orientation {2}".format(
                    piece_id,
                    position_index,
                    corner_orientation[position_index],
                )
            )
        cube_state[piece_index] = state_code

    for position_index, piece_id in enumerate(edge_permutation):
        piece_index = EDGE_PIECE_INDICES[piece_id]
        state_code = EDGE_STATE_CODE_LOOKUPS[piece_id][position_index][edge_orientation[position_index]]
        if state_code is None:
            raise ValueError(
                "Missing edge state for piece {0} at position {1} orientation {2}".format(
                    piece_id,
                    position_index,
                    edge_orientation[position_index],
                )
            )
        cube_state[piece_index] = state_code

    return tuple(cube_state)


def extract_corner_orientation_index_from_state(state):
    corner_orientation = [0] * 8
    for piece_id, piece_index in enumerate(CORNER_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = CORNER_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        corner_orientation[position_index] = get_corner_orientation_from_piece_state(piece_state)
    return encode_corner_orientation(corner_orientation)


def extract_edge_orientation_index_from_state(state):
    edge_orientation = [0] * 12
    for piece_id, piece_index in enumerate(EDGE_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = EDGE_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        edge_orientation[position_index] = EDGE_ORIENTATION_VALUES[piece_id][state[piece_index]]
    return encode_edge_orientation(edge_orientation)


def extract_slice_position_index_from_state(state):
    slice_positions = []
    for piece_id, piece_index in enumerate(EDGE_PIECE_INDICES):
        if piece_id not in SLICE_EDGE_PIECE_SET:
            continue

        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = EDGE_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        slice_positions.append(position_index)

    slice_positions.sort()
    return SLICE_POSITION_TO_INDEX[tuple(slice_positions)]


def extract_corner_permutation_index_from_state(state):
    corner_permutation = [0] * 8
    for piece_id, piece_index in enumerate(CORNER_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = CORNER_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        corner_permutation[position_index] = piece_id
    return rank_permutation(corner_permutation)


def extract_edge_permutation_index_from_state(state):
    edge_permutation = [0] * 8
    for piece_id, piece_index in enumerate(EDGE_PIECE_INDICES):
        compact_piece_id = PHASE2_UD_EDGE_PIECE_TO_INDEX.get(piece_id)
        if compact_piece_id is None:
            continue

        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = EDGE_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        compact_position_index = PHASE2_UD_EDGE_POSITION_TO_INDEX.get(position_index)
        if compact_position_index is None:
            continue

        edge_permutation[compact_position_index] = compact_piece_id

    return rank_permutation(edge_permutation)


def extract_slice_permutation_index_from_state(state):
    slice_permutation = [0] * 4
    for piece_id in SLICE_EDGE_PIECES:
        piece_index = EDGE_PIECE_INDICES[piece_id]
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = EDGE_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        compact_position_index = SLICE_EDGE_POSITION_TO_INDEX.get(position_index)
        if compact_position_index is None:
            continue

        slice_permutation[compact_position_index] = piece_id - SLICE_EDGE_PIECES[0]

    return rank_permutation(slice_permutation)


def build_corner_orientation_state(state_index):
    return build_cube_state_from_coordinates(
        corner_orientation=decode_corner_orientation(state_index)
    )


def build_edge_orientation_state(state_index):
    return build_cube_state_from_coordinates(
        edge_orientation=decode_edge_orientation(state_index)
    )


def build_slice_position_state(state_index):
    selected_positions = SLICE_POSITION_COMBINATIONS[state_index]
    edge_permutation = [-1] * 12

    for offset, position_index in enumerate(selected_positions):
        edge_permutation[position_index] = SLICE_EDGE_PIECES[offset]

    remaining_positions = [
        position_index
        for position_index in range(12)
        if position_index not in selected_positions
    ]
    for offset, position_index in enumerate(remaining_positions):
        edge_permutation[position_index] = NON_SLICE_EDGE_PIECES[offset]

    return build_cube_state_from_coordinates(edge_permutation=tuple(edge_permutation))


def build_corner_permutation_state(state_index):
    return build_cube_state_from_coordinates(
        corner_permutation=unrank_permutation(state_index, 8)
    )


def build_edge_permutation_state(state_index):
    compact_permutation = unrank_permutation(state_index, 8)
    edge_permutation = list(SOLVED_EDGE_PERMUTATION)
    for offset, position_index in enumerate(PHASE2_UD_EDGE_POSITIONS):
        edge_permutation[position_index] = PHASE2_UD_EDGE_PIECES[compact_permutation[offset]]

    return build_cube_state_from_coordinates(edge_permutation=tuple(edge_permutation))


def build_slice_permutation_state(state_index):
    compact_permutation = unrank_permutation(state_index, 4)
    edge_permutation = list(SOLVED_EDGE_PERMUTATION)
    for offset, position_index in enumerate(SLICE_EDGE_POSITIONS):
        edge_permutation[position_index] = SLICE_EDGE_PIECES[compact_permutation[offset]]

    return build_cube_state_from_coordinates(edge_permutation=tuple(edge_permutation))


def build_coordinate_move_table(
    state_count,
    move_names,
    build_state_function,
    extract_index_function,
    label,
):
    log("Building {0} move table...".format(label))
    move_table = array("H", [0]) * (state_count * len(move_names))

    for state_index in range(state_count):
        representative_state = build_state_function(state_index)
        table_offset = state_index * len(move_names)

        for move_offset, move_name in enumerate(move_names):
            next_state = apply_move_to_cube_state(representative_state, move_name)
            move_table[table_offset + move_offset] = extract_index_function(next_state)

        if (state_index + 1) % SOLVER_PROGRESS_STEP == 0:
            process_events()

    return move_table


def build_combined_pruning_table(
    primary_move_table,
    secondary_move_table,
    secondary_size,
    move_count,
    label,
    primary_start_index=0,
    secondary_start_index=0,
):
    log("Building {0} pruning table...".format(label))
    primary_size = len(primary_move_table) // move_count
    pruning_table = bytearray([PRUNING_TABLE_UNVISITED]) * (primary_size * secondary_size)
    start_index = primary_start_index * secondary_size + secondary_start_index
    queue = array("I", [start_index])
    queue_index = 0
    pruning_table[start_index] = 0

    while queue_index < len(queue):
        combined_index = queue[queue_index]
        queue_index += 1
        depth = pruning_table[combined_index]
        primary_index = combined_index // secondary_size
        secondary_index = combined_index % secondary_size
        primary_offset = primary_index * move_count
        secondary_offset = secondary_index * move_count
        next_depth = depth + 1

        for move_offset in range(move_count):
            next_combined_index = (
                primary_move_table[primary_offset + move_offset] * secondary_size
                + secondary_move_table[secondary_offset + move_offset]
            )
            if pruning_table[next_combined_index] != PRUNING_TABLE_UNVISITED:
                continue

            pruning_table[next_combined_index] = next_depth
            queue.append(next_combined_index)

        if queue_index % SOLVER_PROGRESS_STEP == 0:
            process_events()

    return pruning_table


def ensure_two_phase_solver_ready():
    global PHASE1_CORNER_ORIENTATION_MOVE_TABLE
    global PHASE1_EDGE_ORIENTATION_MOVE_TABLE
    global PHASE1_SLICE_POSITION_MOVE_TABLE
    global PHASE2_CORNER_PERMUTATION_MOVE_TABLE
    global PHASE2_EDGE_PERMUTATION_MOVE_TABLE
    global PHASE2_SLICE_PERMUTATION_MOVE_TABLE
    global PHASE1_CORNER_EDGE_PRUNING_TABLE
    global PHASE1_CORNER_SLICE_PRUNING_TABLE
    global PHASE1_EDGE_SLICE_PRUNING_TABLE
    global PHASE2_CORNER_SLICE_PRUNING_TABLE
    global PHASE2_EDGE_SLICE_PRUNING_TABLE
    global PHASE1_REVERSE_FRONTIER_KEYS
    global PHASE1_REVERSE_FRONTIER_DEPTHS
    global PHASE1_REVERSE_FRONTIER_MASK

    ensure_solver_state()
    build_two_phase_solver_metadata()

    if (
        PHASE1_CORNER_ORIENTATION_MOVE_TABLE
        and PHASE1_EDGE_ORIENTATION_MOVE_TABLE
        and PHASE1_SLICE_POSITION_MOVE_TABLE
        and PHASE2_CORNER_PERMUTATION_MOVE_TABLE
        and PHASE2_EDGE_PERMUTATION_MOVE_TABLE
        and PHASE2_SLICE_PERMUTATION_MOVE_TABLE
        and PHASE1_CORNER_EDGE_PRUNING_TABLE
        and PHASE1_CORNER_SLICE_PRUNING_TABLE
        and PHASE1_EDGE_SLICE_PRUNING_TABLE
        and PHASE2_CORNER_SLICE_PRUNING_TABLE
        and PHASE2_EDGE_SLICE_PRUNING_TABLE
        and PHASE1_REVERSE_FRONTIER_KEYS
        and PHASE1_REVERSE_FRONTIER_DEPTHS
        and PHASE1_REVERSE_FRONTIER_MASK
    ):
        return

    if load_two_phase_solver_cache():
        return

    build_start = time.perf_counter()
    PHASE1_CORNER_ORIENTATION_MOVE_TABLE = build_coordinate_move_table(
        CORNER_ORIENTATION_COUNT,
        PHASE1_MOVE_NAMES,
        build_corner_orientation_state,
        extract_corner_orientation_index_from_state,
        "phase-1 corner orientation",
    )
    PHASE1_EDGE_ORIENTATION_MOVE_TABLE = build_coordinate_move_table(
        EDGE_ORIENTATION_COUNT,
        PHASE1_MOVE_NAMES,
        build_edge_orientation_state,
        extract_edge_orientation_index_from_state,
        "phase-1 edge orientation",
    )
    PHASE1_SLICE_POSITION_MOVE_TABLE = build_coordinate_move_table(
        SLICE_POSITION_COUNT,
        PHASE1_MOVE_NAMES,
        build_slice_position_state,
        extract_slice_position_index_from_state,
        "phase-1 slice position",
    )
    PHASE2_CORNER_PERMUTATION_MOVE_TABLE = build_coordinate_move_table(
        CORNER_PERMUTATION_COUNT,
        PHASE2_MOVE_NAMES,
        build_corner_permutation_state,
        extract_corner_permutation_index_from_state,
        "phase-2 corner permutation",
    )
    PHASE2_EDGE_PERMUTATION_MOVE_TABLE = build_coordinate_move_table(
        EDGE_PERMUTATION_COUNT,
        PHASE2_MOVE_NAMES,
        build_edge_permutation_state,
        extract_edge_permutation_index_from_state,
        "phase-2 edge permutation",
    )
    PHASE2_SLICE_PERMUTATION_MOVE_TABLE = build_coordinate_move_table(
        SLICE_PERMUTATION_COUNT,
        PHASE2_MOVE_NAMES,
        build_slice_permutation_state,
        extract_slice_permutation_index_from_state,
        "phase-2 slice permutation",
    )
    PHASE1_CORNER_EDGE_PRUNING_TABLE = build_combined_pruning_table(
        PHASE1_CORNER_ORIENTATION_MOVE_TABLE,
        PHASE1_EDGE_ORIENTATION_MOVE_TABLE,
        EDGE_ORIENTATION_COUNT,
        PHASE1_MOVE_COUNT,
        "phase-1 corner/edge",
    )
    PHASE1_CORNER_SLICE_PRUNING_TABLE = build_combined_pruning_table(
        PHASE1_CORNER_ORIENTATION_MOVE_TABLE,
        PHASE1_SLICE_POSITION_MOVE_TABLE,
        SLICE_POSITION_COUNT,
        PHASE1_MOVE_COUNT,
        "phase-1 corner/slice",
        secondary_start_index=SOLVED_SLICE_POSITION_INDEX,
    )
    PHASE1_EDGE_SLICE_PRUNING_TABLE = build_combined_pruning_table(
        PHASE1_EDGE_ORIENTATION_MOVE_TABLE,
        PHASE1_SLICE_POSITION_MOVE_TABLE,
        SLICE_POSITION_COUNT,
        PHASE1_MOVE_COUNT,
        "phase-1 edge/slice",
        secondary_start_index=SOLVED_SLICE_POSITION_INDEX,
    )
    PHASE2_CORNER_SLICE_PRUNING_TABLE = build_combined_pruning_table(
        PHASE2_CORNER_PERMUTATION_MOVE_TABLE,
        PHASE2_SLICE_PERMUTATION_MOVE_TABLE,
        SLICE_PERMUTATION_COUNT,
        PHASE2_MOVE_COUNT,
        "phase-2 corner/slice",
    )
    PHASE2_EDGE_SLICE_PRUNING_TABLE = build_combined_pruning_table(
        PHASE2_EDGE_PERMUTATION_MOVE_TABLE,
        PHASE2_SLICE_PERMUTATION_MOVE_TABLE,
        SLICE_PERMUTATION_COUNT,
        PHASE2_MOVE_COUNT,
        "phase-2 edge/slice",
    )
    build_phase1_reverse_frontier()
    save_two_phase_solver_cache()
    log(
        "Two-phase solver tables are ready after {0:.2f} seconds".format(
            time.perf_counter() - build_start
        )
    )


def estimate_phase1_remaining_moves(corner_orientation_index, edge_orientation_index, slice_position_index):
    return max(
        PHASE1_CORNER_EDGE_PRUNING_TABLE[
            corner_orientation_index * EDGE_ORIENTATION_COUNT + edge_orientation_index
        ],
        PHASE1_CORNER_SLICE_PRUNING_TABLE[
            corner_orientation_index * SLICE_POSITION_COUNT + slice_position_index
        ],
        PHASE1_EDGE_SLICE_PRUNING_TABLE[
            edge_orientation_index * SLICE_POSITION_COUNT + slice_position_index
        ],
    )


def estimate_phase2_remaining_moves(corner_permutation_index, edge_permutation_index, slice_permutation_index):
    return max(
        PHASE2_CORNER_SLICE_PRUNING_TABLE[
            corner_permutation_index * SLICE_PERMUTATION_COUNT + slice_permutation_index
        ],
        PHASE2_EDGE_SLICE_PRUNING_TABLE[
            edge_permutation_index * SLICE_PERMUTATION_COUNT + slice_permutation_index
        ],
    )


def get_phase1_coordinate_key(corner_orientation_index, edge_orientation_index, slice_position_index):
    return (
        (corner_orientation_index * EDGE_ORIENTATION_COUNT + edge_orientation_index)
        * SLICE_POSITION_COUNT
        + slice_position_index
    )


def decode_phase1_coordinate_key(coordinate_key):
    slice_position_index = coordinate_key % SLICE_POSITION_COUNT
    coordinate_key //= SLICE_POSITION_COUNT
    edge_orientation_index = coordinate_key % EDGE_ORIENTATION_COUNT
    corner_orientation_index = coordinate_key // EDGE_ORIENTATION_COUNT
    return (
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )


def get_phase1_reverse_frontier_slot_count():
    return PHASE1_REVERSE_FRONTIER_SLOT_COUNT_BY_DEPTH.get(
        PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT,
        1 << 24,
    )


def find_phase1_reverse_frontier_slot(keys, mask, coordinate_key):
    slot = (coordinate_key * PHASE1_REVERSE_FRONTIER_HASH_MULTIPLIER) & mask
    while True:
        existing_key = keys[slot]
        if existing_key == PHASE1_REVERSE_FRONTIER_EMPTY_KEY or existing_key == coordinate_key:
            return slot
        slot = (slot + 1) & mask


def get_phase1_reverse_frontier_depth_by_key(coordinate_key):
    if not PHASE1_REVERSE_FRONTIER_KEYS:
        return PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH

    slot = find_phase1_reverse_frontier_slot(
        PHASE1_REVERSE_FRONTIER_KEYS,
        PHASE1_REVERSE_FRONTIER_MASK,
        coordinate_key,
    )
    if PHASE1_REVERSE_FRONTIER_KEYS[slot] == PHASE1_REVERSE_FRONTIER_EMPTY_KEY:
        return PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH
    return PHASE1_REVERSE_FRONTIER_DEPTHS[slot]


def build_phase1_reverse_frontier():
    global PHASE1_REVERSE_FRONTIER_KEYS
    global PHASE1_REVERSE_FRONTIER_DEPTHS
    global PHASE1_REVERSE_FRONTIER_MASK
    global PHASE1_REVERSE_FRONTIER_ENTRY_COUNT

    if PHASE1_REVERSE_FRONTIER_KEYS and PHASE1_REVERSE_FRONTIER_DEPTHS:
        return

    log(
        "Building phase-1 reverse frontier to depth {0}...".format(
            PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT
        )
    )
    slot_count = get_phase1_reverse_frontier_slot_count()
    frontier_keys = array("I", [PHASE1_REVERSE_FRONTIER_EMPTY_KEY]) * slot_count
    frontier_depths = bytearray([PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH]) * slot_count
    frontier_mask = slot_count - 1
    frontier_entry_count = 0

    start_key = get_phase1_coordinate_key(0, 0, SOLVED_SLICE_POSITION_INDEX)
    start_slot = find_phase1_reverse_frontier_slot(frontier_keys, frontier_mask, start_key)
    frontier_keys[start_slot] = start_key
    frontier_depths[start_slot] = 0
    frontier_entry_count = 1
    current_layer = array("I", [start_key])
    processed_count = 0

    for depth in range(PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT):
        next_layer = array("I")
        next_depth = depth + 1

        for coordinate_key in current_layer:
            processed_count += 1
            (
                corner_orientation_index,
                edge_orientation_index,
                slice_position_index,
            ) = decode_phase1_coordinate_key(coordinate_key)
            corner_offset = corner_orientation_index * PHASE1_MOVE_COUNT
            edge_offset = edge_orientation_index * PHASE1_MOVE_COUNT
            slice_offset = slice_position_index * PHASE1_MOVE_COUNT

            for move_offset in range(PHASE1_MOVE_COUNT):
                next_key = get_phase1_coordinate_key(
                    PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset],
                    PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset],
                    PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset],
                )
                slot = find_phase1_reverse_frontier_slot(
                    frontier_keys,
                    frontier_mask,
                    next_key,
                )
                if frontier_keys[slot] != PHASE1_REVERSE_FRONTIER_EMPTY_KEY:
                    continue

                frontier_keys[slot] = next_key
                frontier_depths[slot] = next_depth
                frontier_entry_count += 1
                next_layer.append(next_key)

            if processed_count % SOLVER_PROGRESS_STEP == 0:
                process_events()

        current_layer = next_layer

    PHASE1_REVERSE_FRONTIER_KEYS = frontier_keys
    PHASE1_REVERSE_FRONTIER_DEPTHS = frontier_depths
    PHASE1_REVERSE_FRONTIER_MASK = frontier_mask
    PHASE1_REVERSE_FRONTIER_ENTRY_COUNT = frontier_entry_count
    log(
        "Phase-1 reverse frontier cached {0} states in {1} slots".format(
            frontier_entry_count,
            slot_count,
        )
    )


def build_phase1_suffix_from_frontier(
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
):
    suffix = []
    current_coordinates = (
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    current_depth = get_phase1_reverse_frontier_depth_by_key(
        get_phase1_coordinate_key(*current_coordinates)
    )
    if current_depth == PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH:
        return None

    while current_depth > 0:
        corner_offset = current_coordinates[0] * PHASE1_MOVE_COUNT
        edge_offset = current_coordinates[1] * PHASE1_MOVE_COUNT
        slice_offset = current_coordinates[2] * PHASE1_MOVE_COUNT
        best_next = None

        for move_offset, move_name in enumerate(PHASE1_MOVE_NAMES):
            next_coordinates = (
                PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset],
                PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset],
                PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset],
            )
            next_depth = get_phase1_reverse_frontier_depth_by_key(
                get_phase1_coordinate_key(*next_coordinates)
            )
            if next_depth == PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH or next_depth != current_depth - 1:
                continue

            suffix.append(move_name)
            current_coordinates = next_coordinates
            current_depth = next_depth
            best_next = move_name
            break

        if best_next is None:
            raise ValueError(
                "Phase-1 reverse frontier could not reconstruct a suffix from coordinates {0}".format(
                    (
                        corner_orientation_index,
                        edge_orientation_index,
                        slice_position_index,
                    )
                )
            )

    return suffix


def get_two_phase_node_count(stats):
    return stats["phase1_nodes"] + stats["phase2_nodes"]


def get_search_context_face(last_move):
    if not last_move:
        return None
    return get_move_face(last_move)


def apply_move_sequence_to_cube_state(state, move_names):
    return apply_move_sequence_with_tables(state, MOVE_STATE_TABLES, move_names)


def search_phase2_with_ida_star(
    corner_permutation_index,
    edge_permutation_index,
    slice_permutation_index,
    depth,
    max_depth,
    path,
    last_move,
    stats,
    deadline,
    max_nodes,
    best_depths,
):
    if get_solver_clock_time() >= deadline:
        return {
            "solution": None,
            "hit_limit": True,
            "limit_type": "time",
        }

    heuristic = estimate_phase2_remaining_moves(
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
    )
    if depth + heuristic > max_depth:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }

    if corner_permutation_index == 0 and edge_permutation_index == 0 and slice_permutation_index == 0:
        return {
            "solution": path[:],
            "hit_limit": False,
            "limit_type": None,
        }

    state_key = (
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
        get_search_context_face(last_move),
    )
    previous_best = best_depths.get(state_key)
    if previous_best is not None and depth >= previous_best:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }
    best_depths[state_key] = depth

    if depth >= max_depth:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }

    stats["deepest_phase2"] = max(stats["deepest_phase2"], depth)
    stats["phase2_nodes"] += 1
    if get_two_phase_node_count(stats) >= max_nodes:
        return {
            "solution": None,
            "hit_limit": True,
            "limit_type": "nodes",
        }

    if get_two_phase_node_count(stats) % SOLVER_PROGRESS_STEP == 0:
        process_events()

    corner_offset = corner_permutation_index * PHASE2_MOVE_COUNT
    edge_offset = edge_permutation_index * PHASE2_MOVE_COUNT
    slice_offset = slice_permutation_index * PHASE2_MOVE_COUNT
    ordered_moves = []

    for move_offset, move_name in enumerate(PHASE2_MOVE_NAMES):
        if should_prune_search_move(last_move, move_name):
            continue

        next_corner_permutation_index = PHASE2_CORNER_PERMUTATION_MOVE_TABLE[corner_offset + move_offset]
        next_edge_permutation_index = PHASE2_EDGE_PERMUTATION_MOVE_TABLE[edge_offset + move_offset]
        next_slice_permutation_index = PHASE2_SLICE_PERMUTATION_MOVE_TABLE[slice_offset + move_offset]
        next_heuristic = estimate_phase2_remaining_moves(
            next_corner_permutation_index,
            next_edge_permutation_index,
            next_slice_permutation_index,
        )
        if depth + 1 + next_heuristic > max_depth:
            continue

        ordered_moves.append(
            (
                next_heuristic,
                move_name,
                next_corner_permutation_index,
                next_edge_permutation_index,
                next_slice_permutation_index,
            )
        )

    ordered_moves.sort(key=lambda move_info: move_info[0])
    for (
        _next_heuristic,
        move_name,
        next_corner_permutation_index,
        next_edge_permutation_index,
        next_slice_permutation_index,
    ) in ordered_moves:
        path.append(move_name)
        result = search_phase2_with_ida_star(
            next_corner_permutation_index,
            next_edge_permutation_index,
            next_slice_permutation_index,
            depth + 1,
            max_depth,
            path,
            move_name,
            stats,
            deadline,
            max_nodes,
            best_depths,
        )
        if result["solution"] is not None or result["hit_limit"]:
            return result

        path.pop()

    return {
        "solution": None,
        "hit_limit": False,
        "limit_type": None,
    }


def find_phase2_solution(start_state, stats, deadline, max_nodes, max_depth, last_move=None):
    corner_permutation_index = extract_corner_permutation_index_from_state(start_state)
    edge_permutation_index = extract_edge_permutation_index_from_state(start_state)
    slice_permutation_index = extract_slice_permutation_index_from_state(start_state)
    return find_phase2_solution_from_coordinates(
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
        stats,
        deadline,
        max_nodes,
        max_depth,
        last_move=last_move,
    )


def find_phase2_solution_from_coordinates(
    corner_permutation_index,
    edge_permutation_index,
    slice_permutation_index,
    stats,
    deadline,
    max_nodes,
    max_depth,
    last_move=None,
):
    # Several phase-1 candidates can land on different full states that share
    # the same phase-2 coordinates. Accepting coordinates directly lets the
    # caller reuse them without rebuilding that state each time.
    heuristic = estimate_phase2_remaining_moves(
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
    )
    stats["searched_phase2_bound"] = max(stats["searched_phase2_bound"], max_depth)
    if heuristic > max_depth:
        return None, {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }

    result = search_phase2_with_ida_star(
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
        depth=0,
        max_depth=max_depth,
        path=[],
        last_move=last_move,
        stats=stats,
        deadline=deadline,
        max_nodes=max_nodes,
        best_depths={},
    )
    return result["solution"], result


def maybe_add_phase1_candidate(
    start_state,
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
    path,
    last_move,
    phase1_depth_limit,
    candidate_entries,
):
    # Join the current forward node against the reverse frontier and record the
    # resulting phase-2 entry only if it is the shortest known phase-1 path to
    # that same phase-2 coordinate/context.
    reverse_depth = get_phase1_reverse_frontier_depth_by_key(
        get_phase1_coordinate_key(
            corner_orientation_index,
            edge_orientation_index,
            slice_position_index,
        )
    )
    if reverse_depth == PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH:
        return

    phase1_length = len(path) + reverse_depth
    if phase1_length > phase1_depth_limit:
        return

    phase1_suffix = build_phase1_suffix_from_frontier(
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    phase1_solution = tuple(path) + tuple(phase1_suffix)
    phase2_start_state = apply_move_sequence_to_cube_state(start_state, phase1_solution)
    corner_permutation_index = extract_corner_permutation_index_from_state(phase2_start_state)
    edge_permutation_index = extract_edge_permutation_index_from_state(phase2_start_state)
    slice_permutation_index = extract_slice_permutation_index_from_state(phase2_start_state)
    phase2_heuristic = estimate_phase2_remaining_moves(
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
    )
    candidate_last_move = phase1_solution[-1] if phase1_solution else last_move
    candidate_key = (
        corner_permutation_index,
        edge_permutation_index,
        slice_permutation_index,
        get_search_context_face(candidate_last_move),
    )
    existing_candidate = candidate_entries.get(candidate_key)
    if (
        existing_candidate is not None
        and existing_candidate["phase1_length"] <= phase1_length
    ):
        # A shorter or equal prefix already reaches this exact phase-2 entry,
        # so keeping the longer version would only waste later phase-2 work.
        return

    candidate_entries[candidate_key] = {
        "phase1_solution": phase1_solution,
        "phase1_length": phase1_length,
        "corner_permutation_index": corner_permutation_index,
        "edge_permutation_index": edge_permutation_index,
        "slice_permutation_index": slice_permutation_index,
        "phase2_heuristic": phase2_heuristic,
        "last_move": candidate_last_move,
    }


def collect_phase1_candidates_with_ida_star(
    start_state,
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
    depth,
    forward_depth_limit,
    phase1_depth_limit,
    path,
    last_move,
    stats,
    deadline,
    max_nodes,
    best_depths,
    candidate_entries,
):
    # This is phase 1 without any phase-2 handoff yet: it only discovers all
    # midpoint candidates reachable within the current phase-1 bound.
    if get_solver_clock_time() >= deadline:
        return {
            "hit_limit": True,
            "limit_type": "time",
        }

    heuristic = estimate_phase1_remaining_moves(
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    if depth + heuristic > phase1_depth_limit:
        return {
            "hit_limit": False,
            "limit_type": None,
        }

    state_key = (
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
        get_search_context_face(last_move),
    )
    previous_best = best_depths.get(state_key)
    if previous_best is not None and depth >= previous_best:
        return {
            "hit_limit": False,
            "limit_type": None,
        }
    best_depths[state_key] = depth

    maybe_add_phase1_candidate(
        start_state,
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
        path,
        last_move,
        phase1_depth_limit,
        candidate_entries,
    )

    if depth >= forward_depth_limit:
        return {
            "hit_limit": False,
            "limit_type": None,
        }

    stats["deepest_phase1"] = max(stats["deepest_phase1"], depth)
    stats["phase1_nodes"] += 1
    if get_two_phase_node_count(stats) >= max_nodes:
        return {
            "hit_limit": True,
            "limit_type": "nodes",
        }

    if get_two_phase_node_count(stats) % SOLVER_PROGRESS_STEP == 0:
        process_events()

    corner_offset = corner_orientation_index * PHASE1_MOVE_COUNT
    edge_offset = edge_orientation_index * PHASE1_MOVE_COUNT
    slice_offset = slice_position_index * PHASE1_MOVE_COUNT
    ordered_moves = []

    for move_offset, move_name in enumerate(PHASE1_MOVE_NAMES):
        if should_prune_search_move(last_move, move_name):
            continue

        next_corner_orientation_index = PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset]
        next_edge_orientation_index = PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset]
        next_slice_position_index = PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset]
        next_heuristic = estimate_phase1_remaining_moves(
            next_corner_orientation_index,
            next_edge_orientation_index,
            next_slice_position_index,
        )
        if depth + 1 + next_heuristic > phase1_depth_limit:
            continue

        next_reverse_depth = get_phase1_reverse_frontier_depth_by_key(
            get_phase1_coordinate_key(
                next_corner_orientation_index,
                next_edge_orientation_index,
                next_slice_position_index,
            )
        )
        ordered_moves.append(
            (
                next_reverse_depth,
                next_heuristic,
                move_name,
                next_corner_orientation_index,
                next_edge_orientation_index,
                next_slice_position_index,
            )
        )

    ordered_moves.sort(key=lambda move_info: (move_info[0], move_info[1]))
    for (
        _next_reverse_depth,
        _next_heuristic,
        move_name,
        next_corner_orientation_index,
        next_edge_orientation_index,
        next_slice_position_index,
    ) in ordered_moves:
        path.append(move_name)
        result = collect_phase1_candidates_with_ida_star(
            start_state,
            next_corner_orientation_index,
            next_edge_orientation_index,
            next_slice_position_index,
            depth + 1,
            forward_depth_limit,
            phase1_depth_limit,
            path,
            move_name,
            stats,
            deadline,
            max_nodes,
            best_depths,
            candidate_entries,
        )
        path.pop()

        if result["hit_limit"]:
            return result

    return {
        "hit_limit": False,
        "limit_type": None,
    }


def collect_phase1_candidates_for_bound(
    start_state,
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
    phase1_depth_limit,
    stats,
    deadline,
    max_nodes,
):
    # Phase 1 bounds repeat across several total-depth iterations. Collect the
    # midpoint set once, sort it by promising phase-1/phase-2 cost, then reuse
    # that ordered list while total_bound grows.
    forward_depth_limit = max(
        0,
        phase1_depth_limit - PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT,
    )
    candidate_entries = {}
    result = collect_phase1_candidates_with_ida_star(
        start_state,
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
        depth=0,
        forward_depth_limit=forward_depth_limit,
        phase1_depth_limit=phase1_depth_limit,
        path=[],
        last_move=None,
        stats=stats,
        deadline=deadline,
        max_nodes=max_nodes,
        best_depths={},
        candidate_entries=candidate_entries,
    )
    if result["hit_limit"]:
        return None, result

    candidates = list(candidate_entries.values())
    candidates.sort(
        key=lambda candidate: (
            candidate["phase1_length"] + candidate["phase2_heuristic"],
            candidate["phase2_heuristic"],
            candidate["phase1_length"],
        )
    )
    return candidates, {
        "hit_limit": False,
        "limit_type": None,
    }


def try_phase1_frontier_join(
    start_state,
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
    depth,
    phase1_depth_limit,
    total_bound,
    path,
    last_move,
    stats,
    deadline,
    max_nodes,
):
    reverse_depth = get_phase1_reverse_frontier_depth_by_key(
        get_phase1_coordinate_key(
            corner_orientation_index,
            edge_orientation_index,
            slice_position_index,
        )
    )
    if (
        reverse_depth == PHASE1_REVERSE_FRONTIER_EMPTY_DEPTH
        or depth + reverse_depth > phase1_depth_limit
    ):
        return None

    remaining_phase2_depth = total_bound - (depth + reverse_depth)
    if remaining_phase2_depth < 0:
        return None

    phase1_suffix = build_phase1_suffix_from_frontier(
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    phase1_solution = path[:] + phase1_suffix
    phase2_start_state = apply_move_sequence_to_cube_state(start_state, phase1_solution)
    phase2_solution, phase2_result = find_phase2_solution(
        phase2_start_state,
        stats,
        deadline,
        max_nodes,
        max_depth=min(remaining_phase2_depth, TWO_PHASE_MAX_PHASE2_DEPTH),
        last_move=phase1_solution[-1] if phase1_solution else last_move,
    )
    if phase2_solution is not None:
        return {
            "solution": phase1_solution + phase2_solution,
            "hit_limit": False,
            "limit_type": None,
        }

    if phase2_result["hit_limit"]:
        return phase2_result

    return {
        "solution": None,
        "hit_limit": False,
        "limit_type": None,
    }


def search_phase1_with_ida_star(
    start_state,
    corner_orientation_index,
    edge_orientation_index,
    slice_position_index,
    depth,
    forward_depth_limit,
    phase1_depth_limit,
    total_bound,
    path,
    last_move,
    stats,
    deadline,
    max_nodes,
    best_depths,
):
    if get_solver_clock_time() >= deadline:
        return {
            "solution": None,
            "hit_limit": True,
            "limit_type": "time",
        }

    heuristic = estimate_phase1_remaining_moves(
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    if depth + heuristic > phase1_depth_limit:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }

    join_result = try_phase1_frontier_join(
        start_state,
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
        depth,
        phase1_depth_limit,
        total_bound,
        path,
        last_move,
        stats,
        deadline,
        max_nodes,
    )
    if join_result is not None:
        if join_result["solution"] is not None or join_result["hit_limit"]:
            return join_result

    if depth >= forward_depth_limit:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }

    state_key = (
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
        get_search_context_face(last_move),
    )
    previous_best = best_depths.get(state_key)
    if previous_best is not None and depth >= previous_best:
        return {
            "solution": None,
            "hit_limit": False,
            "limit_type": None,
        }
    best_depths[state_key] = depth

    stats["deepest_phase1"] = max(stats["deepest_phase1"], depth)
    stats["phase1_nodes"] += 1
    if get_two_phase_node_count(stats) >= max_nodes:
        return {
            "solution": None,
            "hit_limit": True,
            "limit_type": "nodes",
        }

    if get_two_phase_node_count(stats) % SOLVER_PROGRESS_STEP == 0:
        process_events()

    corner_offset = corner_orientation_index * PHASE1_MOVE_COUNT
    edge_offset = edge_orientation_index * PHASE1_MOVE_COUNT
    slice_offset = slice_position_index * PHASE1_MOVE_COUNT
    ordered_moves = []

    for move_offset, move_name in enumerate(PHASE1_MOVE_NAMES):
        if should_prune_search_move(last_move, move_name):
            continue

        next_corner_orientation_index = PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset]
        next_edge_orientation_index = PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset]
        next_slice_position_index = PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset]
        next_heuristic = estimate_phase1_remaining_moves(
            next_corner_orientation_index,
            next_edge_orientation_index,
            next_slice_position_index,
        )
        if depth + 1 + next_heuristic > phase1_depth_limit:
            continue

        next_reverse_depth = get_phase1_reverse_frontier_depth_by_key(
            get_phase1_coordinate_key(
                next_corner_orientation_index,
                next_edge_orientation_index,
                next_slice_position_index,
            )
        )
        ordered_moves.append(
            (
                next_reverse_depth,
                next_heuristic,
                move_name,
                next_corner_orientation_index,
                next_edge_orientation_index,
                next_slice_position_index,
            )
        )

    ordered_moves.sort(key=lambda move_info: (move_info[0], move_info[1]))
    for (
        _next_reverse_depth,
        _next_heuristic,
        move_name,
        next_corner_orientation_index,
        next_edge_orientation_index,
        next_slice_position_index,
    ) in ordered_moves:
        path.append(move_name)
        result = search_phase1_with_ida_star(
            start_state,
            next_corner_orientation_index,
            next_edge_orientation_index,
            next_slice_position_index,
            depth + 1,
            forward_depth_limit,
            phase1_depth_limit,
            total_bound,
            path,
            move_name,
            stats,
            deadline,
            max_nodes,
            best_depths,
        )
        if result["solution"] is not None or result["hit_limit"]:
            return result

        path.pop()

    return {
        "solution": None,
        "hit_limit": False,
        "limit_type": None,
    }


def find_two_phase_solution(start_state, max_nodes=None):
    ensure_two_phase_solver_ready()
    if max_nodes is None:
        max_nodes = SOLVER_MAX_NODES

    if start_state == CANONICAL_SOLVED_CUBE_STATE:
        return [], {
            "phase1_nodes": 0,
            "phase2_nodes": 0,
            "deepest_phase1": 0,
            "deepest_phase2": 0,
            "hit_limit": False,
            "limit_type": None,
            "searched_phase1_bound": 0,
            "searched_phase2_bound": 0,
            "searched_total_bound": 0,
        }

    corner_orientation_index = extract_corner_orientation_index_from_state(start_state)
    edge_orientation_index = extract_edge_orientation_index_from_state(start_state)
    slice_position_index = extract_slice_position_index_from_state(start_state)
    minimum_phase1_bound = estimate_phase1_remaining_moves(
        corner_orientation_index,
        edge_orientation_index,
        slice_position_index,
    )
    maximum_total_bound = TWO_PHASE_MAX_PHASE1_DEPTH + TWO_PHASE_MAX_PHASE2_DEPTH
    stats = {
        "phase1_nodes": 0,
        "phase2_nodes": 0,
        "deepest_phase1": 0,
        "deepest_phase2": 0,
        "hit_limit": False,
        "limit_type": None,
        "searched_phase1_bound": minimum_phase1_bound,
        "searched_phase2_bound": 0,
        "searched_total_bound": minimum_phase1_bound,
    }
    deadline = get_solver_clock_time() + SOLVER_MAX_SECONDS
    # Cache phase-1 midpoint lists by bound so later total-depth iterations can
    # spend their extra budget on phase 2 instead of repeating the same phase-1
    # search from scratch.
    phase1_candidates_by_bound = {}

    for total_bound in range(minimum_phase1_bound, maximum_total_bound + 1):
        stats["searched_total_bound"] = total_bound
        stats["searched_phase1_bound"] = min(total_bound, TWO_PHASE_MAX_PHASE1_DEPTH)
        phase1_depth_limit = stats["searched_phase1_bound"]
        phase1_candidates = phase1_candidates_by_bound.get(phase1_depth_limit)
        if phase1_candidates is None:
            phase1_candidates, phase1_result = collect_phase1_candidates_for_bound(
                start_state,
                corner_orientation_index,
                edge_orientation_index,
                slice_position_index,
                phase1_depth_limit=phase1_depth_limit,
                stats=stats,
                deadline=deadline,
                max_nodes=max_nodes,
            )
            if phase1_result["hit_limit"]:
                stats["hit_limit"] = True
                stats["limit_type"] = phase1_result["limit_type"]
                return None, stats
            phase1_candidates_by_bound[phase1_depth_limit] = phase1_candidates

        for candidate in phase1_candidates:
            minimum_total_cost = candidate["phase1_length"] + candidate["phase2_heuristic"]
            if minimum_total_cost > total_bound:
                continue

            # Once phase 1 is fixed, only phase 2 needs to grow with the total
            # bound, so reuse the saved midpoint and give phase 2 the remainder.
            remaining_phase2_depth = total_bound - candidate["phase1_length"]
            if remaining_phase2_depth < 0:
                continue

            phase2_solution, phase2_result = find_phase2_solution_from_coordinates(
                candidate["corner_permutation_index"],
                candidate["edge_permutation_index"],
                candidate["slice_permutation_index"],
                stats,
                deadline,
                max_nodes,
                max_depth=min(remaining_phase2_depth, TWO_PHASE_MAX_PHASE2_DEPTH),
                last_move=candidate["last_move"],
            )

            if phase2_solution is not None:
                return list(candidate["phase1_solution"]) + phase2_solution, stats

            if phase2_result["hit_limit"]:
                stats["hit_limit"] = True
                stats["limit_type"] = phase2_result["limit_type"]
                return None, stats

    stats["hit_limit"] = True
    stats["limit_type"] = "total_depth"
    return None, stats


def initialize_solver_state():
    global SOLVER_PIECES
    global SOLVED_PIECE_STATES
    global PIECE_STATE_INFOS
    global PIECE_STATE_TO_CODE
    global PIECE_POSITION_IDS
    global PIECE_ORIENTATION_IDS
    global MOVE_STATE_TABLES
    global CANONICAL_SOLVED_CUBE_STATE
    global SOLVED_CUBE_STATE
    global CUBE_STATE

    if not SOLVER_PIECES:
        SOLVER_PIECES = build_solver_piece_metadata()
    if not SOLVED_PIECE_STATES:
        SOLVED_PIECE_STATES = build_solved_piece_states()
    if not PIECE_STATE_INFOS or not PIECE_STATE_TO_CODE:
        (
            PIECE_STATE_INFOS,
            PIECE_STATE_TO_CODE,
            PIECE_POSITION_IDS,
            PIECE_ORIENTATION_IDS,
        ) = build_piece_state_catalogs()
    if not MOVE_STATE_TABLES:
        MOVE_STATE_TABLES = build_move_state_tables()

    CANONICAL_SOLVED_CUBE_STATE = build_solved_cube_state()
    SOLVED_CUBE_STATE = CANONICAL_SOLVED_CUBE_STATE
    CUBE_STATE = CANONICAL_SOLVED_CUBE_STATE


def ensure_solver_state():
    global CUBE_STATE

    if (
        not SOLVER_PIECES
        or not SOLVED_PIECE_STATES
        or not PIECE_STATE_INFOS
        or not PIECE_STATE_TO_CODE
        or not MOVE_STATE_TABLES
        or not CANONICAL_SOLVED_CUBE_STATE
        or not SOLVED_CUBE_STATE
    ):
        initialize_solver_state()
        return

    if not CUBE_STATE:
        CUBE_STATE = SOLVED_CUBE_STATE


def get_canonical_solved_state():
    ensure_solver_state()
    return CANONICAL_SOLVED_CUBE_STATE


def apply_move_to_cube_state(state, move_name):
    ensure_solver_state()
    return apply_move_to_cube_state_with_tables(state, MOVE_STATE_TABLES, move_name)


def estimate_remaining_moves(state, goal_state=None):
    ensure_solver_state()
    if goal_state is None:
        goal_state = SOLVED_CUBE_STATE

    misplaced_edges = 0
    misplaced_corners = 0
    misoriented_edges = 0
    misoriented_corners = 0

    for index, piece_state_code in enumerate(state):
        goal_piece_state_code = goal_state[index]
        if piece_state_code == goal_piece_state_code:
            continue

        position_matches = (
            PIECE_POSITION_IDS[index][piece_state_code]
            == PIECE_POSITION_IDS[index][goal_piece_state_code]
        )
        orientation_matches = (
            PIECE_ORIENTATION_IDS[index][piece_state_code]
            == PIECE_ORIENTATION_IDS[index][goal_piece_state_code]
        )

        if SOLVER_PIECES[index]["piece_type"] == "edge":
            if not position_matches:
                misplaced_edges += 1
            if not orientation_matches:
                misoriented_edges += 1
        else:
            if not position_matches:
                misplaced_corners += 1
            if not orientation_matches:
                misoriented_corners += 1

    return max(
        int(math.ceil(misplaced_edges / 4.0)),
        int(math.ceil(misplaced_corners / 4.0)),
        int(math.ceil(misoriented_edges / 4.0)),
        int(math.ceil(misoriented_corners / 4.0)),
    )


def search_with_ida_star(
    state,
    goal_state,
    depth,
    bound,
    path,
    path_states,
    last_move,
    stats,
    max_depth,
    max_nodes,
    deadline,
    best_depths,
    heuristic_cache,
):
    if get_solver_clock_time() >= deadline:
        return {
            "solution": None,
            "next_bound": float("inf"),
            "hit_limit": True,
            "limit_type": "time",
        }

    heuristic = heuristic_cache.get(state)
    if heuristic is None:
        heuristic = estimate_remaining_moves(state, goal_state)
        heuristic_cache[state] = heuristic
    estimated_cost = depth + heuristic
    if estimated_cost > bound:
        return {
            "solution": None,
            "next_bound": estimated_cost,
            "hit_limit": False,
            "limit_type": None,
        }

    previous_best = best_depths.get(state)
    if previous_best is not None and depth >= previous_best:
        return {
            "solution": None,
            "next_bound": float("inf"),
            "hit_limit": False,
            "limit_type": None,
        }
    best_depths[state] = depth

    if state == goal_state:
        return {
            "solution": path[:],
            "next_bound": bound,
            "hit_limit": False,
            "limit_type": None,
        }

    if depth >= max_depth:
        return {
            "solution": None,
            "next_bound": float("inf"),
            "hit_limit": False,
            "limit_type": None,
        }

    stats["deepest_depth"] = max(stats["deepest_depth"], depth)
    stats["expanded_nodes"] += 1
    if stats["expanded_nodes"] >= max_nodes:
        return {
            "solution": None,
            "next_bound": float("inf"),
            "hit_limit": True,
            "limit_type": "nodes",
        }

    if stats["expanded_nodes"] % SOLVER_PROGRESS_STEP == 0:
        process_events()

    next_bound = float("inf")
    for move_name in SEARCH_MOVE_NAMES:
        if should_prune_search_move(last_move, move_name):
            continue

        next_state = apply_move_to_cube_state(state, move_name)
        if next_state in path_states:
            continue

        path.append(move_name)
        path_states.add(next_state)
        result = search_with_ida_star(
            next_state,
            goal_state,
            depth + 1,
            bound,
            path,
            path_states,
            move_name,
            stats,
            max_depth,
            max_nodes,
            deadline,
            best_depths,
            heuristic_cache,
        )
        path_states.remove(next_state)
        if result["solution"] is not None or result["hit_limit"]:
            return result

        path.pop()
        next_bound = min(next_bound, result["next_bound"])

    return {
        "solution": None,
        "next_bound": next_bound,
        "hit_limit": False,
        "limit_type": None,
    }


def find_basic_a_star_solution(start_state, goal_state=None, max_depth=None, max_nodes=None):
    ensure_solver_state()
    if goal_state is None:
        goal_state = SOLVED_CUBE_STATE
    if max_depth is None:
        max_depth = SOLVER_MAX_DEPTH
    if max_nodes is None:
        max_nodes = SOLVER_MAX_NODES

    if start_state == goal_state:
        return [], {
            "expanded_nodes": 0,
            "deepest_depth": 0,
            "hit_limit": False,
            "limit_type": None,
            "searched_bound": 0,
        }

    bound = estimate_remaining_moves(start_state, goal_state)
    stats = {
        "expanded_nodes": 0,
        "deepest_depth": 0,
        "hit_limit": False,
        "limit_type": None,
        "searched_bound": bound,
    }
    deadline = get_solver_clock_time() + SOLVER_MAX_SECONDS
    heuristic_cache = {}

    while bound <= max_depth:
        stats["searched_bound"] = bound
        result = search_with_ida_star(
            start_state,
            goal_state,
            depth=0,
            bound=bound,
            path=[],
            path_states={start_state},
            last_move=None,
            stats=stats,
            max_depth=max_depth,
            max_nodes=max_nodes,
            deadline=deadline,
            best_depths={},
            heuristic_cache=heuristic_cache,
        )

        if result["solution"] is not None:
            return result["solution"], stats

        if result["hit_limit"]:
            stats["hit_limit"] = True
            stats["limit_type"] = result["limit_type"]
            return None, stats

        if result["next_bound"] == float("inf"):
            break

        bound = result["next_bound"]

    stats["hit_limit"] = True
    stats["limit_type"] = "depth"
    return None, stats
