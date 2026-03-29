"""Shared Rubik's Cube move notation helpers."""

import random


MOVES = {
    "U": ("y", 1, -90),
    "U'": ("y", 1, 90),
    "D": ("y", -1, 90),
    "D'": ("y", -1, -90),
    "R": ("x", 1, -90),
    "R'": ("x", 1, 90),
    "L": ("x", -1, 90),
    "L'": ("x", -1, -90),
    "F": ("z", 1, -90),
    "F'": ("z", 1, 90),
    "B": ("z", -1, 90),
    "B'": ("z", -1, -90),
}

CUBE_ROTATIONS = {
    "X": ("x", -90),
    "X'": ("x", 90),
    "Y": ("y", -90),
    "Y'": ("y", 90),
    "Z": ("z", -90),
    "Z'": ("z", 90),
}

FACE_VECTORS = {
    "U": (0, 1, 0),
    "D": (0, -1, 0),
    "R": (1, 0, 0),
    "L": (-1, 0, 0),
    "F": (0, 0, 1),
    "B": (0, 0, -1),
}

FACE_AXES = {
    "U": "y",
    "D": "y",
    "R": "x",
    "L": "x",
    "F": "z",
    "B": "z",
}

OPPOSITE_FACE_ORDER = {
    "U": 0,
    "D": 1,
    "R": 0,
    "L": 1,
    "F": 0,
    "B": 1,
}

SEARCH_MOVES = {
    "U": MOVES["U"],
    "U2": ("y", 1, 180),
    "U'": MOVES["U'"],
    "D": MOVES["D"],
    "D2": ("y", -1, 180),
    "D'": MOVES["D'"],
    "R": MOVES["R"],
    "R2": ("x", 1, 180),
    "R'": MOVES["R'"],
    "L": MOVES["L"],
    "L2": ("x", -1, 180),
    "L'": MOVES["L'"],
    "F": MOVES["F"],
    "F2": ("z", 1, 180),
    "F'": MOVES["F'"],
    "B": MOVES["B"],
    "B2": ("z", -1, 180),
    "B'": MOVES["B'"],
}

SEARCH_MOVE_NAMES = tuple(SEARCH_MOVES.keys())
MOVE_EXPANSIONS = {
    "U": ("U",),
    "U2": ("U", "U"),
    "U'": ("U'",),
    "D": ("D",),
    "D2": ("D", "D"),
    "D'": ("D'",),
    "R": ("R",),
    "R2": ("R", "R"),
    "R'": ("R'",),
    "L": ("L",),
    "L2": ("L", "L"),
    "L'": ("L'",),
    "F": ("F",),
    "F2": ("F", "F"),
    "F'": ("F'",),
    "B": ("B",),
    "B2": ("B", "B"),
    "B'": ("B'",),
}

PHASE1_MOVE_NAMES = SEARCH_MOVE_NAMES
PHASE2_MOVE_NAMES = (
    "U",
    "U2",
    "U'",
    "D",
    "D2",
    "D'",
    "R2",
    "L2",
    "F2",
    "B2",
)

PRIME_TRANSLATION = str.maketrans({
    "\u2019": "'",
    "\u2018": "'",
    "\u2032": "'",
    "\u00b4": "'",
    "\u201c": "\"",
    "\u201d": "\"",
})


def normalize_algorithm_text(sequence_text):
    if not sequence_text:
        return ""

    normalized = sequence_text.translate(PRIME_TRANSLATION)
    for separator in (",", ";", "\n", "\r", "\t"):
        normalized = normalized.replace(separator, " ")
    return " ".join(normalized.split())


def parse_move_sequence(sequence_text, valid_moves=None):
    valid_moves = valid_moves or SEARCH_MOVES
    normalized = normalize_algorithm_text(sequence_text)
    if not normalized:
        return []

    move_names = normalized.split()
    invalid_moves = [move_name for move_name in move_names if move_name not in valid_moves]
    if invalid_moves:
        raise ValueError(
            "Invalid moves: {0}".format(", ".join(invalid_moves))
        )
    return move_names


def format_move_sequence(move_names):
    return " ".join(move_names)


def get_move_face(move_name):
    return move_name[0]


def get_inverse_move(move_name):
    if move_name.endswith("2"):
        return move_name
    if move_name.endswith("'"):
        return move_name.replace("'", "")
    return move_name + "'"


INVERSE_MOVES = {
    move_name: get_inverse_move(move_name)
    for move_name in SEARCH_MOVE_NAMES
}


def invert_move_sequence(move_names):
    return [INVERSE_MOVES.get(move_name, move_name) for move_name in reversed(move_names)]


def reverse_sequence_text(sequence_text):
    return format_move_sequence(invert_move_sequence(parse_move_sequence(sequence_text)))


def expand_solver_moves(move_names):
    expanded = []
    for move_name in move_names:
        expanded.extend(MOVE_EXPANSIONS.get(move_name, (move_name,)))
    return expanded


def should_prune_search_move(last_move, move_name):
    if not last_move:
        return False

    last_face = get_move_face(last_move)
    face = get_move_face(move_name)

    if face == last_face:
        return True

    if FACE_AXES[face] == FACE_AXES[last_face]:
        return OPPOSITE_FACE_ORDER[face] < OPPOSITE_FACE_ORDER[last_face]

    return False


def generate_scramble_moves(length, move_names=None, rng=None):
    rng = rng or random
    move_names = tuple(move_names or MOVES.keys())
    scramble = []
    previous_face = None

    for _unused in range(length):
        valid_moves = [
            move_name
            for move_name in move_names
            if get_move_face(move_name) != previous_face
        ]
        move_name = rng.choice(valid_moves)
        scramble.append(move_name)
        previous_face = get_move_face(move_name)

    return scramble


def generate_scramble_text(length, move_names=None, rng=None):
    return format_move_sequence(generate_scramble_moves(length, move_names=move_names, rng=rng))
