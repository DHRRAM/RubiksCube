"""Shared logical cube-state helpers for Maya and standalone code."""

from rubiks_move_notation import FACE_VECTORS, SEARCH_MOVE_NAMES, SEARCH_MOVES


AXIS_INDEX_BY_NAME = {
    "x": 0,
    "y": 1,
    "z": 2,
}


def rotate_logical_vector(vector, axis, angle):
    turns = (int(round(angle / 90.0)) % 4 + 4) % 4
    rotated = tuple(vector)

    for _unused in range(turns):
        x, y, z = rotated
        if axis == "x":
            rotated = (x, -z, y)
        elif axis == "y":
            rotated = (z, y, -x)
        else:
            rotated = (-y, x, z)

    return rotated


def apply_move_to_piece_state(piece_state, move_name):
    axis, value, angle = SEARCH_MOVES[move_name]
    axis_index = AXIS_INDEX_BY_NAME[axis]
    position = piece_state[0]
    sticker_directions = piece_state[1:]

    if position[axis_index] != value:
        return piece_state

    rotated_position = rotate_logical_vector(position, axis, angle)
    rotated_directions = tuple(
        rotate_logical_vector(direction, axis, angle)
        for direction in sticker_directions
    )
    return (rotated_position,) + rotated_directions


def build_solver_piece_metadata():
    pieces = []

    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                position = (x, y, z)
                magnitude = abs(x) + abs(y) + abs(z)
                if magnitude not in (2, 3):
                    continue

                faces = []
                if y == 1:
                    faces.append("U")
                elif y == -1:
                    faces.append("D")
                if x == 1:
                    faces.append("R")
                elif x == -1:
                    faces.append("L")
                if z == 1:
                    faces.append("F")
                elif z == -1:
                    faces.append("B")

                pieces.append(
                    {
                        "home_position": position,
                        "faces": tuple(faces),
                        "piece_type": "corner" if magnitude == 3 else "edge",
                    }
                )

    pieces.sort(
        key=lambda piece: (
            piece["piece_type"],
            piece["home_position"][1],
            piece["home_position"][0],
            piece["home_position"][2],
        )
    )
    return tuple(pieces)


def build_solved_piece_states(solver_pieces):
    return tuple(
        (piece["home_position"],) + tuple(FACE_VECTORS[face] for face in piece["faces"])
        for piece in solver_pieces
    )


def build_piece_state_catalogs(solved_piece_states):
    state_infos = []
    state_to_code = []
    position_ids = []
    orientation_ids = []

    for solved_piece_state in solved_piece_states:
        queue = [solved_piece_state]
        queue_index = 0
        piece_states = [solved_piece_state]
        piece_state_to_code = {solved_piece_state: 0}

        while queue_index < len(queue):
            current_state = queue[queue_index]
            queue_index += 1
            for move_name in SEARCH_MOVE_NAMES:
                next_state = apply_move_to_piece_state(current_state, move_name)
                if next_state in piece_state_to_code:
                    continue

                piece_state_to_code[next_state] = len(piece_states)
                piece_states.append(next_state)
                queue.append(next_state)

        position_lookup = {}
        orientation_lookup = {}
        piece_position_ids = []
        piece_orientation_ids = []
        for piece_state in piece_states:
            position = piece_state[0]
            orientation = piece_state[1:]
            if position not in position_lookup:
                position_lookup[position] = len(position_lookup)
            if orientation not in orientation_lookup:
                orientation_lookup[orientation] = len(orientation_lookup)

            piece_position_ids.append(position_lookup[position])
            piece_orientation_ids.append(orientation_lookup[orientation])

        state_infos.append(tuple(piece_states))
        state_to_code.append(piece_state_to_code)
        position_ids.append(tuple(piece_position_ids))
        orientation_ids.append(tuple(piece_orientation_ids))

    return (
        tuple(state_infos),
        tuple(state_to_code),
        tuple(position_ids),
        tuple(orientation_ids),
    )


def build_move_state_tables(piece_state_infos, piece_state_to_code):
    move_tables = {}

    for move_name in SEARCH_MOVE_NAMES:
        per_piece_tables = []
        for piece_index, piece_states in enumerate(piece_state_infos):
            piece_lookup = piece_state_to_code[piece_index]
            per_piece_tables.append(
                tuple(
                    piece_lookup[apply_move_to_piece_state(piece_state, move_name)]
                    for piece_state in piece_states
                )
            )

        move_tables[move_name] = tuple(per_piece_tables)

    return move_tables


def build_solved_cube_state(piece_state_to_code, solved_piece_states):
    return tuple(
        piece_state_to_code[piece_index][solved_piece_states[piece_index]]
        for piece_index in range(len(solved_piece_states))
    )


def apply_move_to_cube_state(state, move_state_tables, move_name):
    move_table = move_state_tables[move_name]
    return tuple(
        move_table[piece_index][piece_state_code]
        for piece_index, piece_state_code in enumerate(state)
    )


def apply_move_sequence_to_cube_state(state, move_state_tables, move_names):
    for move_name in move_names:
        state = apply_move_to_cube_state(state, move_state_tables, move_name)
    return state
