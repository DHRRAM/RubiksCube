import math
import importlib
import os
import random
import sys
import time
from array import array
from itertools import combinations

import maya.cmds as cmds
import maya.utils as maya_utils


EARLY_TOOL_DIRECTORY_OPTIONVAR = "RubiksCubeToolDirectory"
EARLY_REQUIRED_MODULES = (
    "rubiks_move_notation.py",
    "rubiks_state_utils.py",
    "rubiks_tool_paths.py",
)


def _bootstrap_project_directory():
    candidate_directories = []
    seen_directories = set()

    def add_candidate(path):
        if not path:
            return

        normalized_path = os.path.abspath(path)
        if os.path.isfile(normalized_path):
            normalized_path = os.path.dirname(normalized_path)
        if not os.path.isdir(normalized_path):
            return

        directory_key = os.path.normcase(normalized_path)
        if directory_key in seen_directories:
            return

        seen_directories.add(directory_key)
        candidate_directories.append(normalized_path)

    add_candidate(globals().get("__file__"))
    code_object = globals().get("_bootstrap_project_directory").__code__
    add_candidate(code_object.co_filename)
    add_candidate(os.environ.get("RUBIKS_CUBE_TOOL_DIR"))
    add_candidate(os.getcwd())
    try:
        if cmds.optionVar(exists=EARLY_TOOL_DIRECTORY_OPTIONVAR):
            add_candidate(cmds.optionVar(q=EARLY_TOOL_DIRECTORY_OPTIONVAR))
    except Exception:
        pass
    try:
        add_candidate(cmds.internalVar(userScriptDir=True))
    except Exception:
        pass
    for directory in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        add_candidate(directory)

    for directory in list(sys.path):
        add_candidate(directory)

    for directory in candidate_directories:
        if all(os.path.exists(os.path.join(directory, module_name)) for module_name in EARLY_REQUIRED_MODULES):
            if directory not in sys.path:
                sys.path.insert(0, directory)
            os.environ["RUBIKS_CUBE_TOOL_DIR"] = directory
            try:
                cmds.optionVar(sv=(EARLY_TOOL_DIRECTORY_OPTIONVAR, directory))
            except Exception:
                pass
            return directory

    try:
        selection = cmds.fileDialog2(
            caption="Select rubiks_cube.py",
            fileFilter="Python Files (*.py)",
            fileMode=1,
            okCaption="Use Selected File",
        )
    except Exception:
        selection = None

    if selection:
        selected_directory = os.path.dirname(os.path.abspath(selection[0]))
        if all(
            os.path.exists(os.path.join(selected_directory, module_name))
            for module_name in EARLY_REQUIRED_MODULES
        ):
            if selected_directory not in sys.path:
                sys.path.insert(0, selected_directory)
            os.environ["RUBIKS_CUBE_TOOL_DIR"] = selected_directory
            try:
                cmds.optionVar(sv=(EARLY_TOOL_DIRECTORY_OPTIONVAR, selected_directory))
            except Exception:
                pass
            return selected_directory
    return None


SCRIPT_DIRECTORY = _bootstrap_project_directory()
from rubiks_move_notation import (
    CUBE_ROTATIONS,
    FACE_VECTORS,
    MOVES,
    PHASE1_MOVE_NAMES,
    PHASE2_MOVE_NAMES,
    SEARCH_MOVE_NAMES,
    expand_solver_moves,
    format_move_sequence,
    generate_scramble_text,
    get_inverse_move,
    get_move_face,
    normalize_algorithm_text,
    parse_move_sequence,
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
from rubiks_tool_paths import (
    TOOL_DIRECTORY_OPTIONVAR,
    add_search_directory,
    find_module_file,
    load_module_from_file,
)

try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    QtCore = None
    QtWidgets = None

try:
    from shiboken2 import wrapInstance
except ImportError:
    wrapInstance = None

try:
    import maya.OpenMayaUI as omui
except ImportError:
    omui = None

standalone_solver_core = None
STANDALONE_SOLVER_IMPORT_ERROR = None
STANDALONE_SOLVER_MODULE_MTIME = None

# This script builds a Rubik's Cube in Maya and provides three main systems:
# 1. Cube creation/material assignment.
# 2. Logical move execution, including animation/keyframe support.
# 3. UI + viewport controls for scrambling, solving, and direct interaction.

# -------------------------
# Config
# -------------------------
SPACING = 1.0
MOVE_DURATION = 10
SCRAMBLE_LENGTH = 20

current_time = 1
ANIMATE_CHECKBOX = "rubikAnimateCheck"
MOVE_SPEED_SLIDER = "rubikMoveSpeedSlider"
ALGORITHM_FIELD = "rubikAlgorithmField"
RUN_ALGORITHM_BUTTON = "runAlgorithmButton"
PLAYBACK_PLAY_BUTTON = "playbackPlayButton"
PLAYBACK_STEP_BACK_BUTTON = "playbackStepBackButton"
PLAYBACK_STEP_FORWARD_BUTTON = "playbackStepForwardButton"
PLAYBACK_UNDO_BUTTON = "playbackUndoButton"
PLAYBACK_REDO_BUTTON = "playbackRedoButton"
PLAYBACK_SCRUB_SLIDER = "playbackScrubSlider"
PLAYBACK_STATUS_TEXT = "playbackStatusText"
ANIMATED_ATTRS = [
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
]
INITIAL_STATE = {}
INITIAL_ORIENTATION = {}
CURRENT_ORIENTATION = {
    "x": [1, 0, 0],
    "y": [0, 1, 0],
    "z": [0, 0, 1],
}
FACE_COLORS = {
    "U": (1, 1, 1),
    "D": (1, 1, 0),
    "R": (0, 0, 1),
    "L": (0, 1, 0),
    "F": (1, 0, 0),
    "B": (1, 0.5, 0),
}
THEME_PRESETS = {
    "Classic": {
        "face_colors": {
            "U": (1.0, 1.0, 1.0),
            "D": (1.0, 0.9, 0.1),
            "R": (0.1, 0.3, 1.0),
            "L": (0.1, 0.8, 0.25),
            "F": (0.95, 0.1, 0.1),
            "B": (1.0, 0.5, 0.05),
        },
        "background": (
            (0.18, 0.2, 0.24),
            (0.24, 0.28, 0.34),
            (0.13, 0.14, 0.16),
        ),
    },
    "Blueprint": {
        "face_colors": {
            "U": (0.92, 0.98, 1.0),
            "D": (0.74, 0.9, 0.98),
            "R": (0.18, 0.58, 1.0),
            "L": (0.14, 0.82, 0.78),
            "F": (1.0, 0.43, 0.32),
            "B": (1.0, 0.72, 0.32),
        },
        "background": (
            (0.05, 0.12, 0.2),
            (0.1, 0.2, 0.32),
            (0.03, 0.07, 0.12),
        ),
    },
    "Sunset Horizon": {
        "face_colors": {
            "U": (1.0, 0.96, 0.84),
            "D": (1.0, 0.83, 0.33),
            "R": (0.95, 0.35, 0.37),
            "L": (0.49, 0.78, 0.66),
            "F": (1.0, 0.58, 0.32),
            "B": (0.74, 0.48, 0.93),
        },
        "background": (
            (0.33, 0.18, 0.16),
            (0.55, 0.31, 0.24),
            (0.14, 0.08, 0.1),
        ),
    },
    "Futuristic Neon": {
        "face_colors": {
            "U": (0.95, 0.96, 0.98),
            "D": (0.48, 0.88, 0.96),
            "R": (0.17, 0.78, 1.0),
            "L": (0.63, 0.86, 0.33),
            "F": (1.0, 0.27, 0.54),
            "B": (0.45, 0.28, 1.0),
        },
        "background": (
            (0.08, 0.1, 0.16),
            (0.14, 0.18, 0.27),
            (0.04, 0.05, 0.09),
        ),
    },
    "Earthy & Organic": {
        "face_colors": {
            "U": (0.94, 0.89, 0.81),
            "D": (0.72, 0.59, 0.44),
            "R": (0.52, 0.38, 0.28),
            "L": (0.35, 0.51, 0.39),
            "F": (0.56, 0.43, 0.3),
            "B": (0.2, 0.25, 0.22),
        },
        "background": (
            (0.18, 0.15, 0.12),
            (0.28, 0.23, 0.18),
            (0.1, 0.08, 0.06),
        ),
    },
    "Sports & Energy": {
        "face_colors": {
            "U": (0.96, 0.97, 0.94),
            "D": (0.61, 0.89, 0.41),
            "R": (0.89, 0.15, 0.12),
            "L": (0.18, 0.18, 0.18),
            "F": (0.99, 0.77, 0.14),
            "B": (0.41, 0.62, 0.38),
        },
        "background": (
            (0.13, 0.14, 0.12),
            (0.21, 0.23, 0.18),
            (0.07, 0.08, 0.06),
        ),
    },
    "Oceanic Serenity": {
        "face_colors": {
            "U": (0.95, 0.98, 1.0),
            "D": (0.8, 0.89, 0.94),
            "R": (0.42, 0.61, 0.75),
            "L": (0.48, 0.76, 0.77),
            "F": (0.36, 0.71, 0.81),
            "B": (0.16, 0.23, 0.42),
        },
        "background": (
            (0.08, 0.13, 0.19),
            (0.14, 0.21, 0.29),
            (0.05, 0.08, 0.13),
        ),
    },
    "Retro 80s Neon": {
        "face_colors": {
            "U": (0.86, 0.82, 0.97),
            "D": (0.72, 0.92, 1.0),
            "R": (0.62, 0.28, 0.86),
            "L": (1.0, 0.73, 0.22),
            "F": (1.0, 0.44, 0.2),
            "B": (0.23, 0.84, 0.96),
        },
        "background": (
            (0.14, 0.07, 0.2),
            (0.23, 0.12, 0.31),
            (0.07, 0.03, 0.11),
        ),
    },
    "Sophisticated Pastels": {
        "face_colors": {
            "U": (0.99, 0.97, 0.95),
            "D": (1.0, 0.86, 0.78),
            "R": (0.92, 0.68, 0.78),
            "L": (0.75, 0.86, 0.74),
            "F": (0.66, 0.79, 0.91),
            "B": (0.83, 0.74, 0.9),
        },
        "background": (
            (0.27, 0.24, 0.26),
            (0.39, 0.35, 0.38),
            (0.15, 0.13, 0.15),
        ),
    },
    "Royal Elegance": {
        "face_colors": {
            "U": (0.97, 0.96, 0.98),
            "D": (0.93, 0.88, 0.7),
            "R": (0.45, 0.33, 0.61),
            "L": (0.69, 0.61, 0.77),
            "F": (0.78, 0.74, 0.78),
            "B": (0.95, 0.87, 0.91),
        },
        "background": (
            (0.16, 0.14, 0.19),
            (0.25, 0.22, 0.3),
            (0.09, 0.08, 0.12),
        ),
    },
}

# MOVE_HISTORY is still useful as a fallback, but solving now prefers the
# logical cube-state search rather than relying on session history alone.
MOVE_HISTORY = []
MOVE_HISTORY_FRAMES = []
MOVE_HISTORY_BASE_FRAME = 1
MOVE_HISTORY_INDEX = 0
VISIBLE_HISTORY_LENGTH = 0
TRACK_MOVES = True
SOLVER_MAX_DEPTH = 14
SOLVER_MAX_NODES = 2000000
SOLVER_MAX_SECONDS = 20.0
SOLVER_PROGRESS_STEP = 5000
# Leave event pumping off during solve by default. Background Maya sessions can
# spend a surprising amount of wall-clock time inside UI updates instead of the
# search itself.
SOLVER_UI_EVENT_PUMP_ENABLED = False
# Use CPU time for solve deadlines so background-window throttling does not eat
# the entire budget before the search has actually done the work.
SOLVER_TIME_SOURCE_LABEL = "CPU"
TWO_PHASE_MAX_PHASE1_DEPTH = 12
TWO_PHASE_MAX_PHASE2_DEPTH = 24
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
PHASE1_REVERSE_FRONTIER_DEPTHS = {}

# These globals track the custom viewport arrows and the selection/Shift-key
# plumbing needed to make them behave like clickable controls instead of normal
# scene objects.
CONTROL_SCRIPT_JOBS = []
CONTROL_SHIFT_DIRECTION = None
CONTROL_SHIFT_EVENT_FILTER = None
CONTROL_SHIFT_TIMER = None
CONTROL_CLICK_ARMED = False
CONTROL_SELECTION_BEFORE_CLICK = []
CONTROL_CLICK_UNDO_DISABLED = False
CONTROL_PROCESSING_SELECTION = False
CONTROL_SKIP_SELECTION_CHANGE = False
UI_MOVE_BUTTONS = {}

# Scrambling runs as a deferred one-move-at-a-time scheduler so the UI stays
# responsive and the same button can request an early stop.
SCRAMBLE_BUTTON = "scrambleButton"
SCRAMBLE_ACTIVE = False
SCRAMBLE_STOP_REQUESTED = False
SCRAMBLE_PENDING_MOVES = []
SCRAMBLE_CACHE_WAS_ENABLED = None
ALGORITHM_CACHE_WAS_ENABLED = None
ALGORITHM_RUN_ACTIVE = False
ALGORITHM_RUN_STOP_REQUESTED = False
ALGORITHM_RUN_PENDING_MOVES = []
PLAYBACK_ACTIVE = False
PLAYBACK_STOP_REQUESTED = False
PLAYBACK_SCRUB_UPDATING = False
SLIDER_GROUP_LABEL_WIDTH = 90
SLIDER_GROUP_FIELD_WIDTH = 45
BUILD_CUBE_BUTTON = "buildCubeButton"
CONTROLS_BUTTON = "controlsButton"
SOLVE_SETTINGS_SECTION = "solveSettingsSection"
MOVE_BUTTONS_SECTION = "moveButtonsSection"
ORIENTATION_SECTION = "orientationSection"
ACTION_SECTION = "actionSection"
ALGORITHM_SECTION = "algorithmSection"
PLAYBACK_SECTION = "playbackSection"
RESET_SECTION = "resetSection"
AESTHETICS_POLISH_SECTION = "aestheticsPolishSection"
AESTHETICS_CONTROLS_SECTION = "aestheticsControlsSection"
AESTHETICS_ENVIRONMENT_SECTION = "aestheticsEnvironmentSection"
AESTHETICS_THEME_SECTION = "aestheticsThemeSection"
AESTHETICS_BEVEL_SECTION = "aestheticsBevelSection"
PRESENTATION_MANAGED_CONTROLS = (
    BUILD_CUBE_BUTTON,
    CONTROLS_BUTTON,
    ORIENTATION_SECTION,
    ALGORITHM_SECTION,
    PLAYBACK_SECTION,
    RESET_SECTION,
)
DEFAULT_VIEWPORT_BACKGROUND = {}
MAYA_DEFAULT_VIEWPORT_BACKGROUND = (0.36, 0.36, 0.36)
VISUAL_REFRESH_TOKEN = 0

MOVE_BUTTON_TOOLTIPS = {
    "U": "Rotate the world-up face clockwise.",
    "D": "Rotate the world-down face clockwise.",
    "R": "Rotate the world-right face clockwise.",
    "L": "Rotate the world-left face clockwise.",
    "F": "Rotate the world-front face clockwise.",
    "B": "Rotate the world-back face clockwise.",
}

ROTATION_BUTTON_TOOLTIPS = {
    "X": "Rotate the whole cube around the X axis.",
    "X'": "Rotate the whole cube around the X axis in reverse.",
    "Y": "Rotate the whole cube around the Y axis.",
    "Y'": "Rotate the whole cube around the Y axis in reverse.",
    "Z": "Rotate the whole cube around the Z axis.",
    "Z'": "Rotate the whole cube around the Z axis in reverse.",
}


# Return the standalone solver core module when it is importable from disk.
def get_standalone_solver_core():
    global standalone_solver_core
    global STANDALONE_SOLVER_IMPORT_ERROR
    global STANDALONE_SOLVER_MODULE_MTIME

    module_name = "rubiks_solver_core"
    module_path = None
    search_directories = []
    seen_directories = set()

    # Maya does not always execute scripts like normal Python modules. Search a
    # few likely roots and remember successful hits in an optionVar so the
    # standalone solver can still be found after Script Editor execution.
    add_search_directory(search_directories, seen_directories, SCRIPT_DIRECTORY)
    add_search_directory(search_directories, seen_directories, getattr(sys.modules.get(__name__), "__file__", None))
    code_object = getattr(get_standalone_solver_core, "__code__", None)
    if code_object is not None:
        add_search_directory(search_directories, seen_directories, code_object.co_filename)
    add_search_directory(search_directories, seen_directories, os.getcwd())
    add_search_directory(search_directories, seen_directories, os.environ.get("RUBIKS_CUBE_TOOL_DIR"))
    try:
        add_search_directory(search_directories, seen_directories, cmds.internalVar(userScriptDir=True))
    except Exception:
        pass
    try:
        if cmds.optionVar(exists=TOOL_DIRECTORY_OPTIONVAR):
            add_search_directory(search_directories, seen_directories, cmds.optionVar(q=TOOL_DIRECTORY_OPTIONVAR))
    except Exception:
        pass

    for path in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        add_search_directory(search_directories, seen_directories, path)
    for path in sys.path:
        add_search_directory(search_directories, seen_directories, path)

    module_path = find_module_file(module_name, search_directories)
    if module_path:
        try:
            cmds.optionVar(sv=(TOOL_DIRECTORY_OPTIONVAR, os.path.dirname(module_path)))
        except Exception:
            pass

    try:
        if module_path:
            module_mtime = os.path.getmtime(module_path)
            if (
                standalone_solver_core is None
                or STANDALONE_SOLVER_MODULE_MTIME is None
                or module_mtime != STANDALONE_SOLVER_MODULE_MTIME
                or os.path.abspath(getattr(standalone_solver_core, "__file__", "")) != module_path
            ):
                standalone_solver_core = load_module_from_file(module_name, module_path)
            STANDALONE_SOLVER_MODULE_MTIME = module_mtime
        elif standalone_solver_core is None:
            standalone_solver_core = importlib.import_module(module_name)
            STANDALONE_SOLVER_MODULE_MTIME = None
        else:
            standalone_solver_core = importlib.reload(standalone_solver_core)
            STANDALONE_SOLVER_MODULE_MTIME = None
    except Exception as error:
        sys.modules.pop(module_name, None)
        standalone_solver_core = None
        STANDALONE_SOLVER_IMPORT_ERROR = error
        return None

    STANDALONE_SOLVER_IMPORT_ERROR = None
    if module_path:
        STANDALONE_SOLVER_MODULE_MTIME = os.path.getmtime(module_path)

    standalone_solver_core.configure(
        max_depth=SOLVER_MAX_DEPTH,
        max_nodes=SOLVER_MAX_NODES,
        max_seconds=SOLVER_MAX_SECONDS,
        progress_step=SOLVER_PROGRESS_STEP,
        phase1_depth=TWO_PHASE_MAX_PHASE1_DEPTH,
        phase2_depth=TWO_PHASE_MAX_PHASE2_DEPTH,
    )
    # The standalone core reuses Maya's logging and optional event pump, but
    # the solver remains functional even when event pumping is disabled.
    standalone_solver_core.set_callbacks(
        log_callback=print,
        progress_callback=process_ui_events,
    )
    return standalone_solver_core

# -------------------------
# Visual Settings
# -------------------------
BEVEL_ENABLED = True
BEVEL_FRACTION = 0.04
BEVEL_SEGMENTS = 4
BEVEL_MITERING = 2 # 0=none, 1=uniform, 2=patch (best)
BEVEL_CHAMFER = True
THEME_NAME = "Classic"
GAP_SPACING = 0.06
STICKER_SCALE = 0.82
STICKER_THICKNESS = 0.04
STICKER_ROUNDNESS = 0.025
CONTROL_SIZE = 1.0
CONTROL_OPACITY = 0.75
SHOW_VIEWPORT_BACKGROUND = False
SHOW_FLOOR_GRID = True
PRESENTATION_MODE = False
DEFAULT_NON_THEME_VISUAL_SETTINGS = {
    "gap_spacing": 0.06,
    "sticker_scale": 0.82,
    "sticker_thickness": 0.04,
    "sticker_roundness": 0.025,
    "control_size": 1.0,
    "control_opacity": 0.75,
    "show_viewport_background": False,
    "show_floor_grid": True,
    "presentation_mode": False,
    "bevel_enabled": True,
    "bevel_fraction": 0.04,
    "bevel_segments": 4,
    "bevel_mitering": 2,
    "bevel_chamfer": True,
}

# -------------------------
# UI Settings Callbacks
# -------------------------
# Store the bevel enabled flag from the UI.
def set_bevel_enabled(val):
    global BEVEL_ENABLED
    BEVEL_ENABLED = val

# Store the bevel width from the UI.
def set_bevel_fraction(val):
    global BEVEL_FRACTION
    BEVEL_FRACTION = val

# Store the bevel segment count from the UI.
def set_bevel_segments(val):
    global BEVEL_SEGMENTS
    BEVEL_SEGMENTS = val
    
# Store the bevel mitering mode from the UI.
def set_bevel_mitering(val):
    global BEVEL_MITERING
    BEVEL_MITERING = val

# Store whether bevel corners should be chamfered.
def set_bevel_chamfer(val):
    global BEVEL_CHAMFER
    BEVEL_CHAMFER = val

def set_gap_spacing(val):
    global GAP_SPACING
    GAP_SPACING = val


def set_sticker_scale(val):
    global STICKER_SCALE
    STICKER_SCALE = val


def set_sticker_thickness(val):
    global STICKER_THICKNESS
    STICKER_THICKNESS = val


def set_sticker_roundness(val):
    global STICKER_ROUNDNESS
    STICKER_ROUNDNESS = val


def set_control_size(val):
    global CONTROL_SIZE
    CONTROL_SIZE = val


def set_control_opacity(val):
    global CONTROL_OPACITY
    CONTROL_OPACITY = val


def set_show_viewport_background(val):
    global SHOW_VIEWPORT_BACKGROUND
    SHOW_VIEWPORT_BACKGROUND = val


def set_show_floor_grid(val):
    global SHOW_FLOOR_GRID
    SHOW_FLOOR_GRID = val


def set_presentation_mode(val):
    global PRESENTATION_MODE
    PRESENTATION_MODE = val


def apply_theme(theme_name):
    global THEME_NAME
    if theme_name not in THEME_PRESETS:
        return

    THEME_NAME = theme_name
    FACE_COLORS.update(THEME_PRESETS[theme_name]["face_colors"])


def rebuild_cube_from_visual_settings():
    create_rubiks_cube(preserve_scene_state=True)


def rebuild_viewport_controls_from_settings():
    if cmds.objExists("rubik_controls_grp"):
        setup_viewport_controls()


def schedule_visual_refresh(rebuild_cube=False, rebuild_controls=False, refresh_controls=False, delay_ms=120):
    global VISUAL_REFRESH_TOKEN

    VISUAL_REFRESH_TOKEN += 1
    refresh_token = VISUAL_REFRESH_TOKEN

    def run_refresh():
        if refresh_token != VISUAL_REFRESH_TOKEN:
            return

        if rebuild_cube:
            create_rubiks_cube(preserve_scene_state=True)
            return

        if rebuild_controls:
            rebuild_viewport_controls_from_settings()
        elif refresh_controls:
            refresh_control_materials()

        apply_viewport_background()
        apply_floor_grid_visibility()

    if QtCore is not None and delay_ms > 0:
        QtCore.QTimer.singleShot(delay_ms, run_refresh)
        return

    maya_utils.executeDeferred(run_refresh)


def capture_default_viewport_background():
    if DEFAULT_VIEWPORT_BACKGROUND:
        return
    DEFAULT_VIEWPORT_BACKGROUND.update(
        {
            "background": MAYA_DEFAULT_VIEWPORT_BACKGROUND,
            "backgroundTop": MAYA_DEFAULT_VIEWPORT_BACKGROUND,
            "backgroundBottom": MAYA_DEFAULT_VIEWPORT_BACKGROUND,
        }
    )


def apply_viewport_background():
    capture_default_viewport_background()

    if not DEFAULT_VIEWPORT_BACKGROUND:
        return

    if SHOW_VIEWPORT_BACKGROUND:
        background_top, background_mid, background_bottom = THEME_PRESETS[THEME_NAME]["background"]
        try:
            cmds.displayPref(displayGradient=True)
        except Exception:
            pass
        cmds.displayRGBColor("backgroundTop", *background_top)
        cmds.displayRGBColor("background", *background_mid)
        cmds.displayRGBColor("backgroundBottom", *background_bottom)
        return

    try:
        cmds.displayPref(displayGradient=False)
    except Exception:
        pass
    for color_name, color_value in DEFAULT_VIEWPORT_BACKGROUND.items():
        cmds.displayRGBColor(color_name, *color_value)


def apply_floor_grid_visibility():
    try:
        cmds.grid(toggle=SHOW_FLOOR_GRID)
    except Exception:
        pass


def refresh_control_materials():
    for ctrl_name in ("ctrl_U", "ctrl_D", "ctrl_R", "ctrl_L", "ctrl_F", "ctrl_B"):
        if not cmds.objExists(ctrl_name):
            continue

        logical_face = get_face_from_control_position(ctrl_name)
        color = FACE_COLORS.get(logical_face, (1, 1, 1))
        shader_name = ctrl_name + "_fill_mat"
        if cmds.objExists(shader_name):
            cmds.setAttr(shader_name + ".baseColor", *color, type="double3")
            cmds.setAttr(
                shader_name + ".opacity",
                CONTROL_OPACITY,
                CONTROL_OPACITY,
                CONTROL_OPACITY,
                type="double3",
            )

        for shape in cmds.listRelatives(ctrl_name, shapes=True, fullPath=True) or []:
            if cmds.nodeType(shape) != "nurbsCurve":
                continue
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideRGBColors", 1)
            cmds.setAttr(shape + ".overrideColorRGB", *color)
            cmds.setAttr(shape + ".lineWidth", max(1.0, 2.0 * CONTROL_SIZE))


def set_ui_element_visibility(element_name, visible):
    if cmds.control(element_name, exists=True):
        cmds.control(element_name, e=True, manage=visible, visible=visible)
        return

    if cmds.layout(element_name, exists=True):
        cmds.layout(element_name, e=True, manage=visible, visible=visible)


def update_presentation_mode_ui():
    for control_name in PRESENTATION_MANAGED_CONTROLS:
        set_ui_element_visibility(control_name, not PRESENTATION_MODE)


def sync_visual_settings_ui():
    if cmds.control("gapSpacingSlider", exists=True):
        cmds.floatSliderGrp("gapSpacingSlider", e=True, value=GAP_SPACING)
    if cmds.control("stickerScaleSlider", exists=True):
        cmds.floatSliderGrp("stickerScaleSlider", e=True, value=STICKER_SCALE)
    if cmds.control("stickerThicknessSlider", exists=True):
        cmds.floatSliderGrp("stickerThicknessSlider", e=True, value=STICKER_THICKNESS)
    if cmds.control("stickerRoundnessSlider", exists=True):
        cmds.floatSliderGrp("stickerRoundnessSlider", e=True, value=STICKER_ROUNDNESS)
    if cmds.control("controlSizeSlider", exists=True):
        cmds.floatSliderGrp("controlSizeSlider", e=True, value=CONTROL_SIZE)
    if cmds.control("controlOpacitySlider", exists=True):
        cmds.floatSliderGrp("controlOpacitySlider", e=True, value=CONTROL_OPACITY)
    if cmds.control("viewportBackgroundToggle", exists=True):
        cmds.checkBox("viewportBackgroundToggle", e=True, value=SHOW_VIEWPORT_BACKGROUND)
    if cmds.control("floorGridToggle", exists=True):
        cmds.checkBox("floorGridToggle", e=True, value=SHOW_FLOOR_GRID)
    if cmds.control("presentationModeToggle", exists=True):
        cmds.checkBox("presentationModeToggle", e=True, value=PRESENTATION_MODE)
    if cmds.control("bevelToggle", exists=True):
        cmds.checkBox("bevelToggle", e=True, value=BEVEL_ENABLED)
    if cmds.control("bevelFraction", exists=True):
        cmds.floatSliderGrp("bevelFraction", e=True, value=BEVEL_FRACTION)
    if cmds.control("bevelSegments", exists=True):
        cmds.intSliderGrp("bevelSegments", e=True, value=BEVEL_SEGMENTS)
    if cmds.control("chamferToggle", exists=True):
        cmds.checkBox("chamferToggle", e=True, value=BEVEL_CHAMFER)
    if cmds.control("miteringDropdown", exists=True):
        cmds.optionMenu("miteringDropdown", e=True, value=str(BEVEL_MITERING))
def reset_aesthetics_to_defaults(*_unused):
    global GAP_SPACING
    global STICKER_SCALE
    global STICKER_THICKNESS
    global STICKER_ROUNDNESS
    global CONTROL_SIZE
    global CONTROL_OPACITY
    global SHOW_VIEWPORT_BACKGROUND
    global SHOW_FLOOR_GRID
    global PRESENTATION_MODE
    global BEVEL_ENABLED
    global BEVEL_FRACTION
    global BEVEL_SEGMENTS
    global BEVEL_MITERING
    global BEVEL_CHAMFER

    GAP_SPACING = DEFAULT_NON_THEME_VISUAL_SETTINGS["gap_spacing"]
    STICKER_SCALE = DEFAULT_NON_THEME_VISUAL_SETTINGS["sticker_scale"]
    STICKER_THICKNESS = DEFAULT_NON_THEME_VISUAL_SETTINGS["sticker_thickness"]
    STICKER_ROUNDNESS = DEFAULT_NON_THEME_VISUAL_SETTINGS["sticker_roundness"]
    CONTROL_SIZE = DEFAULT_NON_THEME_VISUAL_SETTINGS["control_size"]
    CONTROL_OPACITY = DEFAULT_NON_THEME_VISUAL_SETTINGS["control_opacity"]
    SHOW_VIEWPORT_BACKGROUND = DEFAULT_NON_THEME_VISUAL_SETTINGS["show_viewport_background"]
    SHOW_FLOOR_GRID = DEFAULT_NON_THEME_VISUAL_SETTINGS["show_floor_grid"]
    PRESENTATION_MODE = DEFAULT_NON_THEME_VISUAL_SETTINGS["presentation_mode"]
    BEVEL_ENABLED = DEFAULT_NON_THEME_VISUAL_SETTINGS["bevel_enabled"]
    BEVEL_FRACTION = DEFAULT_NON_THEME_VISUAL_SETTINGS["bevel_fraction"]
    BEVEL_SEGMENTS = DEFAULT_NON_THEME_VISUAL_SETTINGS["bevel_segments"]
    BEVEL_MITERING = DEFAULT_NON_THEME_VISUAL_SETTINGS["bevel_mitering"]
    BEVEL_CHAMFER = DEFAULT_NON_THEME_VISUAL_SETTINGS["bevel_chamfer"]

    sync_visual_settings_ui()
    apply_viewport_background()
    apply_floor_grid_visibility()
    update_presentation_mode_ui()
    schedule_visual_refresh(rebuild_cube=True, delay_ms=0)


def on_theme_changed(theme_name):
    apply_theme(theme_name)
    apply_viewport_background()
    rebuild_cube_from_visual_settings()


def on_control_opacity_changed(live_update=True):
    global CONTROL_OPACITY

    CONTROL_OPACITY = cmds.floatSliderGrp("controlOpacitySlider", q=True, value=True)
    schedule_visual_refresh(
        refresh_controls=True,
        delay_ms=120 if live_update else 0,
    )


def on_control_size_changed(live_update=True):
    global CONTROL_SIZE

    CONTROL_SIZE = cmds.floatSliderGrp("controlSizeSlider", q=True, value=True)
    schedule_visual_refresh(
        rebuild_controls=True,
        delay_ms=120 if live_update else 0,
    )


def on_gap_spacing_changed(live_update=True):
    global GAP_SPACING

    GAP_SPACING = cmds.floatSliderGrp("gapSpacingSlider", q=True, value=True)
    schedule_visual_refresh(
        rebuild_cube=True,
        delay_ms=120 if live_update else 0,
    )


def on_sticker_scale_changed(live_update=True):
    global STICKER_SCALE

    STICKER_SCALE = cmds.floatSliderGrp("stickerScaleSlider", q=True, value=True)
    schedule_visual_refresh(
        rebuild_cube=True,
        delay_ms=120 if live_update else 0,
    )


def on_sticker_thickness_changed(live_update=True):
    global STICKER_THICKNESS

    STICKER_THICKNESS = cmds.floatSliderGrp("stickerThicknessSlider", q=True, value=True)
    schedule_visual_refresh(
        rebuild_cube=True,
        delay_ms=120 if live_update else 0,
    )


def on_sticker_roundness_changed(live_update=True):
    global STICKER_ROUNDNESS

    STICKER_ROUNDNESS = cmds.floatSliderGrp("stickerRoundnessSlider", q=True, value=True)
    schedule_visual_refresh(
        rebuild_cube=True,
        delay_ms=120 if live_update else 0,
    )


def on_floor_grid_toggled(value):
    set_show_floor_grid(value)
    apply_floor_grid_visibility()


def on_viewport_background_toggled(value):
    set_show_viewport_background(value)
    apply_viewport_background()


def on_presentation_mode_toggled(value):
    set_presentation_mode(value)
    update_presentation_mode_ui()
    
# React to bevel width changes and rebuild the cube.
def on_bevel_fraction_changed():
    global BEVEL_FRACTION

    BEVEL_FRACTION = cmds.floatSliderGrp("bevelFraction", q=True, value=True)
    create_rubiks_cube(preserve_scene_state=True)

# React to bevel segment changes and rebuild the cube.
def on_bevel_segments_changed():
    global BEVEL_SEGMENTS

    BEVEL_SEGMENTS = cmds.intSliderGrp("bevelSegments", q=True, value=True)
    create_rubiks_cube(preserve_scene_state=True)

# -------------------------
# Material Helpers
# -------------------------
# Create or update a named material and its shading group.
def create_material(name, color):
    # Ensure Arnold is loaded
    if not cmds.pluginInfo("mtoa", query=True, loaded=True):
        cmds.loadPlugin("mtoa")

    shader_exists = cmds.objExists(name)
    sg_name = name + "SG"
    sg_exists = cmds.objExists(sg_name)

    # Create shader if missing
    if not shader_exists:
        shader = cmds.shadingNode("aiStandardSurface", asShader=True, name=name)
    else:
        shader = name

    if cmds.attributeQuery("baseColor", node=shader, exists=True):
        cmds.setAttr(shader + ".baseColor", *color, type="double3")

    # Create SG if missing
    if not sg_exists:
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    else:
        sg = sg_name

        # Ensure connection exists
        connections = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
        if shader not in connections:
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)

    return shader, sg

# -------------------------
# Material Presets
# -------------------------
# Build the material lookup used when coloring cubies.
def setup_materials():
    face_colors = THEME_PRESETS[THEME_NAME]["face_colors"]
    return {
        "white": create_material("rubiks_white", face_colors["U"]),
        "yellow": create_material("rubiks_yellow", face_colors["D"]),
        "red": create_material("rubiks_red", face_colors["F"]),
        "orange": create_material("rubiks_orange", face_colors["B"]),
        "blue": create_material("rubiks_blue", face_colors["R"]),
        "green": create_material("rubiks_green", face_colors["L"]),
        "black": create_material("rubiks_black", (0.02, 0.02, 0.02)),
    }


# Delete only the shader nodes created by this tool so rerunning the script
# starts from a clean material state without touching unrelated scene shaders.
def delete_existing_tool_materials():
    material_names = [
        "rubiks_white",
        "rubiks_yellow",
        "rubiks_red",
        "rubiks_orange",
        "rubiks_blue",
        "rubiks_green",
        "rubiks_black",
    ]
    control_names = ["ctrl_U", "ctrl_D", "ctrl_R", "ctrl_L", "ctrl_F", "ctrl_B"]
    shading_nodes = []

    for material_name in material_names:
        shading_nodes.append(material_name + "SG")
        shading_nodes.append(material_name)

    for control_name in control_names:
        shading_nodes.append(control_name + "_fill_matSG")
        shading_nodes.append(control_name + "_fill_mat")

    existing_nodes = [node for node in shading_nodes if cmds.objExists(node)]
    if existing_nodes:
        cmds.delete(existing_nodes)
    
# -------------------------
# Cubie Face Coloring
# -------------------------
# Color the visible faces of one cubie based on its grid position.
def assign_face_materials(cube, materials, pos):
    faces = cmds.polyEvaluate(cube, face=True)
    
    # With separate sticker geometry, the cubie body should read as dark
    # plastic rather than competing with the sticker colors underneath.
    for i in range(faces):
        cmds.sets(
            f"{cube}.f[{i}]",
            e=True,
            forceElement=materials["black"][1]
        )


def get_visible_face_specs(pos):
    face_specs = []
    if pos[1] == 1:
        face_specs.append(((0, 1, 0), "white"))
    if pos[1] == -1:
        face_specs.append(((0, -1, 0), "yellow"))
    if pos[0] == 1:
        face_specs.append(((1, 0, 0), "blue"))
    if pos[0] == -1:
        face_specs.append(((-1, 0, 0), "green"))
    if pos[2] == 1:
        face_specs.append(((0, 0, 1), "red"))
    if pos[2] == -1:
        face_specs.append(((0, 0, -1), "orange"))
    return face_specs


def build_sticker_outline_points(sticker_size, corner_radius):
    half_size = sticker_size * 0.5
    radius = max(0.0, min(corner_radius, half_size))

    if radius <= 1e-5:
        return [
            (-half_size, half_size, 0.0),
            (half_size, half_size, 0.0),
            (half_size, -half_size, 0.0),
            (-half_size, -half_size, 0.0),
        ]

    if radius >= half_size - 1e-5:
        points = []
        circle_steps = 28
        for index in range(circle_steps):
            angle = (math.pi * 2.0 * float(index)) / float(circle_steps)
            points.append(
                (
                    math.cos(angle) * half_size,
                    math.sin(angle) * half_size,
                    0.0,
                )
            )
        return points

    arc_steps = max(4, int(5 + ((radius / half_size) * 7.0)))
    points = []
    corner_specs = (
        ((half_size - radius, half_size - radius), 0.0, math.pi * 0.5),
        ((-half_size + radius, half_size - radius), math.pi * 0.5, math.pi),
        ((-half_size + radius, -half_size + radius), math.pi, math.pi * 1.5),
        ((half_size - radius, -half_size + radius), math.pi * 1.5, math.pi * 2.0),
    )

    for corner_index, (center, start_angle, end_angle) in enumerate(corner_specs):
        for step_index in range(arc_steps + 1):
            if corner_index > 0 and step_index == 0:
                continue
            blend = float(step_index) / float(arc_steps)
            angle = start_angle + ((end_angle - start_angle) * blend)
            points.append(
                (
                    center[0] + (math.cos(angle) * radius),
                    center[1] + (math.sin(angle) * radius),
                    0.0,
                )
            )

    return points


def create_sticker_mesh(name, normal, sticker_size, sticker_depth, sticker_roundness):
    outline_points = build_sticker_outline_points(
        sticker_size,
        sticker_roundness,
    )
    sticker = cmds.polyCreateFacet(
        p=outline_points,
        ch=False,
        name=name,
    )[0]
    front_face_info = cmds.polyInfo(sticker + ".f[0]", fn=True) or []
    if front_face_info:
        values = front_face_info[0].split()
        if float(values[4]) < 0.0:
            cmds.polyNormal(sticker, normalMode=0, userNormalMode=0)
    cmds.polyExtrudeFacet(
        sticker + ".f[0]",
        localTranslateZ=sticker_depth,
    )
    cmds.delete(sticker, constructionHistory=True)
    cmds.xform(sticker, os=True, t=(0.0, 0.0, -(sticker_depth * 0.5)))

    if normal == (1, 0, 0):
        cmds.rotate(0, 90, 0, sticker, os=True)
    elif normal == (-1, 0, 0):
        cmds.rotate(0, -90, 0, sticker, os=True)
    elif normal == (0, 1, 0):
        cmds.rotate(-90, 0, 0, sticker, os=True)
    elif normal == (0, -1, 0):
        cmds.rotate(90, 0, 0, sticker, os=True)
    elif normal == (0, 0, -1):
        cmds.rotate(180, 0, 0, sticker, os=True)

    cmds.makeIdentity(sticker, apply=True, t=1, r=1, s=1)
    # Keep the cap-to-side transition crisp while preserving smoothing around
    # the rounded perimeter segments.
    cmds.polySoftEdge(sticker, angle=60)
    for shape in cmds.listRelatives(sticker, shapes=True, fullPath=True) or []:
        if cmds.nodeType(shape) != "mesh":
            continue
        cmds.setAttr(shape + ".doubleSided", 1)
        cmds.setAttr(shape + ".opposite", 0)
    return sticker


def get_sticker_position_for_face(cube, normal, sticker_depth):
    bbox = cmds.exactWorldBoundingBox(cube)
    center_x = (bbox[0] + bbox[3]) * 0.5
    center_y = (bbox[1] + bbox[4]) * 0.5
    center_z = (bbox[2] + bbox[5]) * 0.5
    surface_padding = 0.001

    if normal == (1, 0, 0):
        return (bbox[3] + (sticker_depth * 0.5) + surface_padding, center_y, center_z)
    if normal == (-1, 0, 0):
        return (bbox[0] - (sticker_depth * 0.5) - surface_padding, center_y, center_z)
    if normal == (0, 1, 0):
        return (center_x, bbox[4] + (sticker_depth * 0.5) + surface_padding, center_z)
    if normal == (0, -1, 0):
        return (center_x, bbox[1] - (sticker_depth * 0.5) - surface_padding, center_z)
    if normal == (0, 0, 1):
        return (center_x, center_y, bbox[5] + (sticker_depth * 0.5) + surface_padding)
    return (center_x, center_y, bbox[2] - (sticker_depth * 0.5) - surface_padding)


def align_sticker_to_cubie_face(cube, sticker, normal):
    cube_bbox = cmds.exactWorldBoundingBox(cube)
    sticker_bbox = cmds.exactWorldBoundingBox(sticker)
    tx, ty, tz = cmds.xform(sticker, q=True, ws=True, t=True)
    surface_padding = 0.001

    if normal == (1, 0, 0):
        cmds.xform(sticker, ws=True, t=(tx + ((cube_bbox[3] + surface_padding) - sticker_bbox[0]), ty, tz))
        return
    if normal == (-1, 0, 0):
        cmds.xform(sticker, ws=True, t=(tx + ((cube_bbox[0] - surface_padding) - sticker_bbox[3]), ty, tz))
        return
    if normal == (0, 1, 0):
        cmds.xform(sticker, ws=True, t=(tx, ty + ((cube_bbox[4] + surface_padding) - sticker_bbox[1]), tz))
        return
    if normal == (0, -1, 0):
        cmds.xform(sticker, ws=True, t=(tx, ty + ((cube_bbox[1] - surface_padding) - sticker_bbox[4]), tz))
        return
    if normal == (0, 0, 1):
        cmds.xform(sticker, ws=True, t=(tx, ty, tz + ((cube_bbox[5] + surface_padding) - sticker_bbox[2])))
        return
    cmds.xform(sticker, ws=True, t=(tx, ty, tz + ((cube_bbox[2] - surface_padding) - sticker_bbox[5])))


def add_stickers_to_cubie(cube, materials, pos, cubie_size):
    sticker_size = max(0.05, cubie_size * STICKER_SCALE)
    sticker_depth = max(0.005, STICKER_THICKNESS)

    for normal, material_key in get_visible_face_specs(pos):
        sticker = create_sticker_mesh(
            name="{0}_{1}_sticker".format(cube, material_key),
            normal=normal,
            sticker_size=sticker_size,
            sticker_depth=sticker_depth,
            sticker_roundness=STICKER_ROUNDNESS,
        )

        sticker_position = get_sticker_position_for_face(cube, normal, sticker_depth)
        cmds.xform(sticker, ws=True, t=sticker_position)
        align_sticker_to_cubie_face(cube, sticker, normal)
        cmds.polySoftEdge(sticker, angle=60)
        cmds.sets(sticker, e=True, forceElement=materials[material_key][1])
        cmds.parent(sticker, cube)

# -------------------------
# Cube Construction
# -------------------------
# Rebuild the full 3x3x3 Rubik's Cube mesh set.
def create_rubiks_cube(preserve_scene_state=False):
    global MOVE_HISTORY_BASE_FRAME
    global MOVE_HISTORY_INDEX
    global VISIBLE_HISTORY_LENGTH

    preserved_state = None
    if preserve_scene_state and get_all_cubies():
        preserved_state = snapshot_visual_rebuild_state()

    materials = setup_materials()
    
    # Delete old cubes
    existing = get_all_cubies()
    if existing:
        cmds.delete(existing)
    
    size = max(0.35, SPACING - GAP_SPACING)
    
    for x in [-1, 0, 1]:
        for y in [-1, 0, 1]:
            for z in [-1, 0, 1]:
                cube_name = make_cubie_identity(x, y, z)
                cube = cmds.polyCube(w=size, h=size, d=size, ch=False, name=cube_name)[0]
                if not cmds.attributeQuery("isRubikCubie", node=cube, exists=True):
                    cmds.addAttr(cube, longName="isRubikCubie", attributeType="bool")
                cmds.setAttr(cube + ".isRubikCubie", 1)
                if not cmds.attributeQuery("cubieId", node=cube, exists=True):
                    cmds.addAttr(cube, longName="cubieId", dataType="string")
                cmds.setAttr(cube + ".cubieId", cube_name, type="string")
                
                cmds.polySoftEdge(cube, angle=0)               
                
                if BEVEL_ENABLED:
                    bevel_node = cmds.polyBevel3(
                        cube,
                        offset=BEVEL_FRACTION,
                        segments=BEVEL_SEGMENTS,
                        mitering=BEVEL_MITERING,
                        chamfer=BEVEL_CHAMFER,
                        subdivideNgons=True
                    )[0]
                    
                    cmds.setAttr(bevel_node + ".offset", BEVEL_FRACTION)
                    
                    # Switch normals after beveling    
                    cmds.polySoftEdge(cube, angle=180)
                    
                    cmds.delete(cube, constructionHistory=True)
                      
                cmds.xform(
                    cube,
                    ws=True,
                    t=(x * SPACING, y * SPACING, z * SPACING),
                )
                
                assign_face_materials(cube, materials, (x, y, z))
                add_stickers_to_cubie(cube, materials, (x, y, z), size)

    reset_orientation()
    initialize_solver_state()
    if preserved_state is None:
        MOVE_HISTORY.clear()
        MOVE_HISTORY_FRAMES.clear()
        MOVE_HISTORY_BASE_FRAME = 1
        MOVE_HISTORY_INDEX = 0
        VISIBLE_HISTORY_LENGTH = 0
        sync_timeline_to_history_index(0)
        capture_initial_state()
        update_ui_move_buttons()
        update_playback_ui()
    else:
        restore_visual_rebuild_state(preserved_state)

    if cmds.objExists("rubik_controls_grp"):
        setup_viewport_controls()
    else:
        reset_viewport_control_directions()
    apply_viewport_background()
    apply_floor_grid_visibility()

# -------------------------
# Scene State And Orientation
# -------------------------
# Return all transform nodes that belong to cubies, not controls.
def get_all_cubies():
    objs = cmds.ls(type="transform")
    cubies = []

    for obj in objs:
        if obj == "rubik_controls_grp" or obj.startswith("ctrl_"):
            continue

        parents = cmds.listRelatives(obj, parent=True, fullPath=False) or []
        if "rubik_controls_grp" in parents:
            continue

        if cmds.attributeQuery("isRubikCubie", node=obj, exists=True) and cmds.getAttr(obj + ".isRubikCubie"):
            cubies.append(obj)

    return cubies

# Return the viewport control transforms, if they exist.
def get_viewport_controls():
    if not cmds.objExists("rubik_controls_grp"):
        return []

    return cmds.listRelatives("rubik_controls_grp", children=True, type="transform", fullPath=False) or []


# Capture the cube's current matrices/orientation as the reset state.
def capture_initial_state(frame=None):
    global INITIAL_STATE
    global INITIAL_ORIENTATION

    cubies = get_all_cubies()
    if frame is None:
        # Save a snapped world matrix so "reset" returns to clean cube-aligned
        # values instead of accumulating floating-point drift.
        INITIAL_STATE = {
            cubie: flatten_matrix(
                snap_matrix(matrix_from_list(cmds.xform(cubie, q=True, ws=True, matrix=True)))
            )
            for cubie in cubies
        }
        INITIAL_ORIENTATION = {
            axis: vector[:]
            for axis, vector in CURRENT_ORIENTATION.items()
        }
        return

    original_time = get_current_frame()
    cmds.currentTime(frame)

    try:
        INITIAL_STATE = {
            cubie: flatten_matrix(
                snap_matrix(matrix_from_list(cmds.xform(cubie, q=True, ws=True, matrix=True)))
            )
            for cubie in cubies
        }
        INITIAL_ORIENTATION = {
            axis: vector[:]
            for axis, vector in CURRENT_ORIENTATION.items()
        }
    finally:
        cmds.currentTime(original_time)


# Ensure a valid reset state exists for the current cube instance.
def ensure_initial_state():
    # The saved reset state should always match the currently existing cubies.
    # If the cube has been rebuilt, we capture a fresh baseline automatically.
    cubies = get_all_cubies()
    if not cubies:
        return

    if not INITIAL_STATE:
        capture_initial_state(frame=1)
        return

    missing_cubies = any(not cmds.objExists(cubie) for cubie in INITIAL_STATE)
    new_cubies = any(cubie not in INITIAL_STATE for cubie in cubies)
    if missing_cubies or new_cubies:
        capture_initial_state(frame=1)


# Save the current pose as the new reset pose.
def save_current_pose_as_initial_state(*_unused):
    global MOVE_HISTORY_BASE_FRAME
    global MOVE_HISTORY_INDEX
    global VISIBLE_HISTORY_LENGTH

    capture_initial_state()
    save_solver_goal_from_current_state()
    MOVE_HISTORY.clear()
    MOVE_HISTORY_FRAMES.clear()
    MOVE_HISTORY_BASE_FRAME = get_current_frame()
    MOVE_HISTORY_INDEX = 0
    VISIBLE_HISTORY_LENGTH = 0
    update_playback_ui()
    print("Saved current cube pose as the reset state")


# Restore logical orientation tracking to the default cube axes.
def reset_orientation():
    global CURRENT_ORIENTATION

    CURRENT_ORIENTATION = {
        "x": [1, 0, 0],
        "y": [0, 1, 0],
        "z": [0, 0, 1],
    }

# Rotate a basis vector in 90-degree steps around one axis.
def rotate_vector_90(vector, axis, angle):
    # Orientation is tracked as basis vectors. Rotating those basis vectors lets
    # us remap logical cube faces (U/R/F/etc.) after whole-cube rotations.
    turns = (int(round(angle / 90.0)) % 4 + 4) % 4
    rotated = vector[:]

    for _unused in range(turns):
        x, y, z = rotated
        if axis == "x":
            rotated = [x, -z, y]
        elif axis == "y":
            rotated = [z, y, -x]
        else:
            rotated = [-y, x, z]

    return rotated

# Update the tracked logical cube orientation after a whole-cube rotation.
def update_current_orientation(axis, angle):
    global CURRENT_ORIENTATION

    CURRENT_ORIENTATION = {
        orientation_axis: rotate_vector_90(vector, axis, angle)
        for orientation_axis, vector in CURRENT_ORIENTATION.items()
    }

# Convert an orientation vector into a world axis name and sign.
def get_world_axis_and_sign(vector):
    axis_index = max(range(3), key=lambda index: abs(vector[index]))
    axis = ("x", "y", "z")[axis_index]
    sign = 1 if vector[axis_index] >= 0 else -1
    return axis, sign

# Map a world-space direction vector to the logical cube face it represents.
def get_logical_face_from_world_vector(world_vector):
    # CURRENT_ORIENTATION answers "which logical cube face is currently pointing
    # along each world axis?" This keeps the UI labels/moves correct even after
    # rotating the entire cube in world space.
    for axis, positive_face, negative_face in (
        ("x", "R", "L"),
        ("y", "U", "D"),
        ("z", "F", "B"),
    ):
        orientation_vector = CURRENT_ORIENTATION[axis]
        if world_vector == orientation_vector:
            return positive_face
        if world_vector == [-component for component in orientation_vector]:
            return negative_face

    return None

# Convert a world face label into the current logical face label.
def get_logical_face_for_world_face(world_face):
    world_vectors = {
        "R": [1, 0, 0],
        "L": [-1, 0, 0],
        "U": [0, 1, 0],
        "D": [0, -1, 0],
        "F": [0, 0, 1],
        "B": [0, 0, -1],
    }
    return get_logical_face_from_world_vector(world_vectors[world_face])

# Convert a logical move name into a world-space slice rotation.
def get_world_rotation_for_move(move_name):
    # MOVES are defined in logical cube space. Before rotating actual Maya
    # objects, convert that logical move into the current world-space axis/sign.
    logical_axis, logical_value, logical_angle = MOVES[move_name]
    world_axis, world_sign = get_world_axis_and_sign(CURRENT_ORIENTATION[logical_axis])
    return (
        world_axis,
        logical_value * world_sign,
        logical_angle * world_sign,
    )

# Find which logical move matches a desired world-facing button press.
def get_mapped_move_for_world_button(world_face, direction):
    desired_move_name = world_face if direction == 1 else world_face + "'"
    desired_rotation = MOVES[desired_move_name]

    for move_name in MOVES:
        if get_world_rotation_for_move(move_name) == desired_rotation:
            return move_name

    return desired_move_name

# Apply one logical cube move, optionally animating and tracking history.
def apply_move(move_name, animate=None, start_frame=None, track_history=None):
    global CUBE_STATE
    global MOVE_HISTORY_BASE_FRAME

    if move_name not in MOVES:
        print("Invalid move")
        return False

    if animate is None:
        animate = get_animation_enabled()

    if track_history is None:
        track_history = TRACK_MOVES

    if track_history and not MOVE_HISTORY and MOVE_HISTORY_INDEX == 0:
        if start_frame is not None:
            MOVE_HISTORY_BASE_FRAME = start_frame
        elif animate:
            MOVE_HISTORY_BASE_FRAME = max(get_current_frame(), current_time)
        else:
            MOVE_HISTORY_BASE_FRAME = get_current_frame()

    world_axis, world_value, world_angle = get_world_rotation_for_move(move_name)
    if not rotate_slice(
        world_axis,
        world_value,
        world_angle,
        animate=animate,
        start_frame=start_frame,
    ):
        return False

    if track_history:
        record_history_move(move_name)
        sync_timeline_to_history_index()

    ensure_solver_state()
    CUBE_STATE = apply_move_to_cube_state(CUBE_STATE, move_name)
    update_playback_ui()
    return True

# -------------------------
# Slice Lookup
# -------------------------
# Return the cubies that belong to one x/y/z layer.
def get_cubies_on_axis(axis="y", value=1):
    cubies = get_all_cubies()
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]

    positions = [cmds.xform(cubie, q=True, ws=True, t=True)[axis_index] for cubie in cubies]
    layers = sorted(list(set(round(position, 2) for position in positions)))

    if len(layers) < 3:
        return []

    mapping = {-1: layers[0], 0: layers[1], 1: layers[2]}
    target = mapping[value]

    result = []
    for cubie in cubies:
        position = cmds.xform(cubie, q=True, ws=True, t=True)
        if abs(position[axis_index] - target) < 0.2:
            result.append(cubie)

    return result


# -------------------------
# Transform Math
# -------------------------
# Compute the average world-space center of a cubie group.
def get_slice_center(cubies):
    positions = [cmds.xform(cubie, q=True, ws=True, t=True) for cubie in cubies]
    count = float(len(positions))
    return [sum(position[i] for position in positions) / count for i in range(3)]


# Convert Maya's flat 16-value matrix list into a 4x4 matrix.
def matrix_from_list(values):
    return [list(values[index:index + 4]) for index in range(0, 16, 4)]


# Convert a 4x4 matrix back into Maya's flat list format.
def flatten_matrix(matrix):
    return [matrix[row][col] for row in range(4) for col in range(4)]


# Multiply two 4x4 transformation matrices.
def multiply_matrices(a, b):
    result = [[0.0] * 4 for _ in range(4)]
    for row in range(4):
        for col in range(4):
            result[row][col] = sum(a[row][step] * b[step][col] for step in range(4))
    return result


# Build a translation matrix from x/y/z offsets.
def translation_matrix(tx, ty, tz):
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [tx, ty, tz, 1.0],
    ]


# Build a rotation matrix for one axis and angle in degrees.
def rotation_matrix(axis, angle_deg):
    angle = math.radians(angle_deg)
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)

    if axis == "x":
        return [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cos_angle, sin_angle, 0.0],
            [0.0, -sin_angle, cos_angle, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    if axis == "y":
        return [
            [cos_angle, 0.0, -sin_angle, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [sin_angle, 0.0, cos_angle, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    if axis == "z":
        return [
            [cos_angle, sin_angle, 0.0, 0.0],
            [-sin_angle, cos_angle, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]

    raise ValueError("Invalid axis: {0}".format(axis))


# Build the transform that rotates around a slice center in world space.
def orbit_matrix(center, axis, angle_deg):
    # Translate the slice to the origin, rotate it, then translate it back.
    cx, cy, cz = center
    return multiply_matrices(
        multiply_matrices(
            translation_matrix(-cx, -cy, -cz),
            rotation_matrix(axis, angle_deg),
        ),
        translation_matrix(cx, cy, cz),
    )


# Snap tiny rotation values to exact -1/0/1 values when possible.
def snap_rotation_value(value):
    nearest = round(value)
    if nearest in (-1, 0, 1) and abs(value - nearest) < 1e-6:
        return float(nearest)
    return value


# Snap a transform matrix back onto the cube grid/orientation.
def snap_matrix(matrix):
    # After repeated animated moves, floating-point noise can leave cubies at
    # values like 0.999999. Snapping keeps positions/orientations grid-aligned.
    snapped = [row[:] for row in matrix]

    for row in range(3):
        for col in range(3):
            snapped[row][col] = snap_rotation_value(snapped[row][col])

    for axis in range(3):
        snapped[3][axis] = round(snapped[3][axis] / SPACING) * SPACING

    snapped[0][3] = 0.0
    snapped[1][3] = 0.0
    snapped[2][3] = 0.0
    snapped[3][3] = 1.0

    return snapped


# Read the current Maya timeline frame as an integer.
def get_current_frame():
    return int(round(cmds.currentTime(q=True)))


# Read whether animation/keyframing is enabled in the UI.
def get_animation_enabled():
    if cmds.control(ANIMATE_CHECKBOX, exists=True):
        return cmds.checkBox(ANIMATE_CHECKBOX, q=True, value=True)
    return True


def get_move_duration():
    global MOVE_DURATION

    if cmds.control(MOVE_SPEED_SLIDER, exists=True):
        MOVE_DURATION = cmds.intSliderGrp(MOVE_SPEED_SLIDER, q=True, value=True)
    return MOVE_DURATION


def is_busy_with_sequence():
    return SCRAMBLE_ACTIVE or ALGORITHM_RUN_ACTIVE or PLAYBACK_ACTIVE


def record_history_move(move_name):
    global VISIBLE_HISTORY_LENGTH
    global MOVE_HISTORY_INDEX

    if MOVE_HISTORY_INDEX < len(MOVE_HISTORY):
        del MOVE_HISTORY[MOVE_HISTORY_INDEX:]
        del MOVE_HISTORY_FRAMES[MOVE_HISTORY_INDEX:]

    MOVE_HISTORY.append(move_name)
    MOVE_HISTORY_FRAMES.append(current_time)
    MOVE_HISTORY_INDEX = len(MOVE_HISTORY)
    VISIBLE_HISTORY_LENGTH = MOVE_HISTORY_INDEX


def can_step_backward():
    return MOVE_HISTORY_INDEX > 0


def can_step_forward():
    return MOVE_HISTORY_INDEX < len(MOVE_HISTORY)


def get_history_frame(index=None):
    if index is None:
        index = MOVE_HISTORY_INDEX

    if index <= 0 or not MOVE_HISTORY_FRAMES:
        return MOVE_HISTORY_BASE_FRAME

    clamped_index = min(index, len(MOVE_HISTORY_FRAMES))
    return MOVE_HISTORY_FRAMES[clamped_index - 1]


def sync_timeline_to_history_index(index=None):
    global current_time

    frame = get_history_frame(index)
    current_time = frame
    cmds.currentTime(frame)


def can_use_keyed_history_scrub():
    return bool(MOVE_HISTORY_FRAMES) and len(MOVE_HISTORY_FRAMES) == len(MOVE_HISTORY)


def sync_logical_state_to_history_index(index):
    global CUBE_STATE
    global MOVE_HISTORY_INDEX

    target_index = max(0, min(int(index), len(MOVE_HISTORY)))
    history_state = SOLVED_CUBE_STATE
    for move_name in MOVE_HISTORY[:target_index]:
        history_state = apply_move_to_cube_state(history_state, move_name)

    CUBE_STATE = history_state
    MOVE_HISTORY_INDEX = target_index


def restore_cube_to_history_base_state():
    global current_time
    global CURRENT_ORIENTATION
    global CUBE_STATE

    controls_enabled = cmds.objExists("rubik_controls_grp")
    ensure_initial_state()
    cubies = [cubie for cubie in INITIAL_STATE if cmds.objExists(cubie)]
    if not cubies:
        print("No saved initial state found")
        return False

    clear_transform_keys(cubies)
    base_frame = get_history_frame(0)
    cmds.currentTime(base_frame)

    for cubie in cubies:
        cmds.xform(cubie, ws=True, matrix=INITIAL_STATE[cubie])

    if INITIAL_ORIENTATION:
        CURRENT_ORIENTATION = {
            axis: vector[:]
            for axis, vector in INITIAL_ORIENTATION.items()
        }
    else:
        reset_orientation()

    ensure_solver_state()
    CUBE_STATE = SOLVED_CUBE_STATE

    if controls_enabled:
        setup_viewport_controls()
    else:
        reset_viewport_control_directions()

    update_ui_move_buttons()
    current_time = base_frame
    return True


def rebuild_scene_to_history_index(target_index):
    global MOVE_HISTORY_INDEX

    target_index = max(0, min(int(target_index), len(MOVE_HISTORY)))
    if not restore_cube_to_history_base_state():
        return False

    for move_name in MOVE_HISTORY[:target_index]:
        if not apply_move(move_name, animate=False, track_history=False):
            return False

    MOVE_HISTORY_INDEX = target_index
    sync_timeline_to_history_index()
    update_playback_ui()
    return True


def get_move_button_tooltip(world_face, direction):
    base_tooltip = MOVE_BUTTON_TOOLTIPS.get(world_face, "Rotate this face.")
    if direction == 1:
        return base_tooltip

    if base_tooltip.endswith("."):
        base_tooltip = base_tooltip[:-1]
    return base_tooltip + " Use the inverse direction."


def get_slider_group_layout_kwargs():
    return {
        "adjustableColumn": 3,
        "columnWidth3": (
            SLIDER_GROUP_LABEL_WIDTH,
            SLIDER_GROUP_FIELD_WIDTH,
            1,
        ),
        "columnAlign3": ("left", "left", "left"),
    }


# Remove translation/rotation keys from a cubie set.
def clear_transform_keys(cubies, time_range=None):
    if not cubies:
        return

    kwargs = {"clear": True}
    if time_range is not None:
        kwargs["time"] = time_range

    for attr in ANIMATED_ATTRS:
        cmds.cutKey(cubies, at=attr, **kwargs)


def make_cubie_identity(x, y, z):
    return "cubie_x{0}_y{1}_z{2}".format(int(x), int(y), int(z))


def get_cubie_identity_from_matrix(matrix_values):
    tx = matrix_values[12] / float(SPACING) if SPACING else matrix_values[12]
    ty = matrix_values[13] / float(SPACING) if SPACING else matrix_values[13]
    tz = matrix_values[14] / float(SPACING) if SPACING else matrix_values[14]

    def clamp_axis(value):
        return max(-1, min(1, int(round(value))))

    return make_cubie_identity(clamp_axis(tx), clamp_axis(ty), clamp_axis(tz))


def get_cubie_identity(cubie):
    if cmds.attributeQuery("cubieId", node=cubie, exists=True):
        cubie_id = cmds.getAttr(cubie + ".cubieId")
        if cubie_id:
            return cubie_id

    if cubie in INITIAL_STATE:
        return get_cubie_identity_from_matrix(INITIAL_STATE[cubie])

    return get_cubie_identity_from_matrix(cmds.xform(cubie, q=True, ws=True, matrix=True))


def capture_cubie_animation_data(cubies):
    animation_data = {}

    for cubie in cubies:
        cubie_id = get_cubie_identity(cubie)
        attr_data = {}
        for attr in ANIMATED_ATTRS:
            times = cmds.keyframe(cubie, at=attr, q=True, tc=True) or []
            values = cmds.keyframe(cubie, at=attr, q=True, vc=True) or []
            attr_data[attr] = list(zip(times, values))
        animation_data[cubie_id] = attr_data

    return animation_data


def restore_cubie_animation_data(animation_data):
    if not animation_data:
        return

    for cubie in get_all_cubies():
        cubie_id = get_cubie_identity(cubie)
        attr_data = animation_data.get(cubie_id)
        if not attr_data:
            continue

        for attr, keys in attr_data.items():
            for frame, value in keys:
                cmds.setKeyframe(cubie, at=attr, t=frame, v=value)

            if keys:
                cmds.keyTangent(cubie, at=attr, itt="linear", ott="linear")


def snapshot_visual_rebuild_state():
    cubies = get_all_cubies()
    return {
        "current_frame": get_current_frame(),
        "current_time": current_time,
        "current_orientation": {
            axis: vector[:]
            for axis, vector in CURRENT_ORIENTATION.items()
        },
        "initial_orientation": {
            axis: vector[:]
            for axis, vector in INITIAL_ORIENTATION.items()
        },
        "initial_state": {
            get_cubie_identity(cubie): matrix_values[:]
            for cubie, matrix_values in INITIAL_STATE.items()
        },
        "current_matrices": {
            get_cubie_identity(cubie): cmds.xform(cubie, q=True, ws=True, matrix=True)
            for cubie in cubies
        },
        "animation_data": capture_cubie_animation_data(cubies),
        "history": MOVE_HISTORY[:],
        "history_frames": MOVE_HISTORY_FRAMES[:],
        "history_base_frame": MOVE_HISTORY_BASE_FRAME,
        "history_index": MOVE_HISTORY_INDEX,
        "visible_history_length": VISIBLE_HISTORY_LENGTH,
        "solver_goal_state": SOLVED_CUBE_STATE,
        "cube_state": CUBE_STATE,
    }


def restore_visual_rebuild_state(state):
    global INITIAL_STATE
    global INITIAL_ORIENTATION
    global CURRENT_ORIENTATION
    global MOVE_HISTORY_BASE_FRAME
    global MOVE_HISTORY_INDEX
    global VISIBLE_HISTORY_LENGTH
    global SOLVED_CUBE_STATE
    global CUBE_STATE
    global current_time

    if not state:
        return

    INITIAL_STATE = {
        cubie_id: matrix_values[:]
        for cubie_id, matrix_values in state["initial_state"].items()
        if cmds.objExists(cubie_id)
    }
    INITIAL_ORIENTATION = {
        axis: vector[:]
        for axis, vector in state["initial_orientation"].items()
    }
    CURRENT_ORIENTATION = {
        axis: vector[:]
        for axis, vector in state["current_orientation"].items()
    }
    MOVE_HISTORY[:] = state["history"]
    MOVE_HISTORY_FRAMES[:] = state["history_frames"]
    MOVE_HISTORY_BASE_FRAME = state["history_base_frame"]
    MOVE_HISTORY_INDEX = state["history_index"]
    VISIBLE_HISTORY_LENGTH = state["visible_history_length"]
    SOLVED_CUBE_STATE = state["solver_goal_state"]
    CUBE_STATE = state["cube_state"]
    current_time = state["current_time"]

    restore_cubie_animation_data(state["animation_data"])

    if any(keys for attr_data in state["animation_data"].values() for keys in attr_data.values()):
        cmds.currentTime(state["current_frame"])
    else:
        for cubie in get_all_cubies():
            cubie_id = get_cubie_identity(cubie)
            matrix_values = state["current_matrices"].get(cubie_id)
            if matrix_values:
                cmds.xform(cubie, ws=True, matrix=matrix_values)
        cmds.currentTime(state["current_frame"])

    update_ui_move_buttons()
    update_playback_ui()


# Set translation and rotation keys for a cubie set on one frame.
def set_transform_keys(cubies, frame):
    if not cubies:
        return

    for cubie in cubies:
        cmds.setKeyframe(cubie, at="translate", t=frame)
        cmds.setKeyframe(cubie, at="rotate", t=frame)


# Find the latest transform keyframe across a cubie set.
def get_last_transform_keyframe(cubies):
    if not cubies:
        return None

    last_frame = None
    for attr in ANIMATED_ATTRS:
        key_times = cmds.keyframe(cubies, at=attr, q=True, tc=True) or []
        if key_times:
            attr_last = max(key_times)
            if last_frame is None or attr_last > last_frame:
                last_frame = attr_last

    return int(round(last_frame)) if last_frame is not None else None


# Let Qt process pending UI events during long operations.
def process_ui_events():
    # Solves intentionally skip this by default because keeping Maya responsive
    # is less important than giving the search stable timing.
    if not SOLVER_UI_EVENT_PUMP_ENABLED:
        return

    if QtWidgets is None:
        return

    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.processEvents()


def get_solver_clock_time():
    # thread_time/process_time tracks actual CPU usage more closely than wall
    # clock time, which makes the solve budget less sensitive to focus changes.
    clock = getattr(time, "thread_time", None)
    if clock is not None:
        return clock()
    return time.process_time()


# Rotate a group of cubies immediately or across an animated frame range.
def rotate_cubies(cubies, axis, angle, animate=True, start_frame=None, clear_future=False):
    global current_time

    if not cubies:
        print("No cubies found for rotation")
        return

    print("Rotating {0} cubies".format(len(cubies)))

    if not animate:
        # In non-animated mode we can apply the final transform immediately.
        center = get_slice_center(cubies)
        orbit = orbit_matrix(center, axis, angle)
        initial_matrices = {
            cubie: matrix_from_list(cmds.xform(cubie, q=True, ws=True, matrix=True))
            for cubie in cubies
        }

        for cubie in cubies:
            world_matrix = snap_matrix(multiply_matrices(initial_matrices[cubie], orbit))
            cmds.xform(cubie, ws=True, matrix=flatten_matrix(world_matrix))

        current_time = get_current_frame()
        return

    start = max(get_current_frame(), current_time) if start_frame is None else start_frame
    end = start + get_move_duration()
    frame_count = max(1, end - start)

    # When animating a slice move, we snapshot every cubie in the scene so we
    # can safely clear/rebuild keys from a known world-space state.
    all_cubies = list(dict.fromkeys(get_all_cubies() + list(cubies)))

    cmds.currentTime(start)

    start_matrices = {
        cubie: matrix_from_list(cmds.xform(cubie, q=True, ws=True, matrix=True))
        for cubie in all_cubies
    }
    center = get_slice_center(cubies)
    initial_matrices = {cubie: start_matrices[cubie] for cubie in cubies}

    if clear_future:
        # Rebuilding keys from the current frame prevents old queued animation
        # from fighting with newly requested moves.
        last_keyframe = get_last_transform_keyframe(all_cubies)
        if last_keyframe is not None and last_keyframe >= start:
            clear_transform_keys(all_cubies, time_range=(start, last_keyframe))

        for cubie, matrix in start_matrices.items():
            cmds.xform(cubie, ws=True, matrix=flatten_matrix(matrix))

        set_transform_keys(all_cubies, start)

    for frame in range(start, end + 1):
        progress = float(frame - start) / float(frame_count)
        orbit = orbit_matrix(center, axis, angle * progress)

        cmds.currentTime(frame)
        for cubie in cubies:
            # Each frame is computed from the original matrix plus the partial
            # orbit, which avoids compounding tiny errors across the loop.
            world_matrix = multiply_matrices(initial_matrices[cubie], orbit)
            if frame == end:
                world_matrix = snap_matrix(world_matrix)

            cmds.xform(cubie, ws=True, matrix=flatten_matrix(world_matrix))
            cmds.setKeyframe(cubie, at="translate", t=frame)
            cmds.setKeyframe(cubie, at="rotate", t=frame)

        process_ui_events()

    for cubie in cubies:
        cmds.keyTangent(cubie, itt="linear", ott="linear")

    current_time = end + 2

# Build a flat mesh strip along a polyline for viewport control shapes.
def create_flat_arc_mesh(points, width=0.05):
    verts = []
    faces = []

    for i in range(len(points) - 1):
        p0 = points[i]
        p1 = points[i + 1]

        dx = p1[0] - p0[0]
        dz = p1[2] - p0[2]

        length = math.sqrt(dx*dx + dz*dz) or 1
        dx /= length
        dz /= length

        px = -dz
        pz = dx

        v0 = (p0[0] + px * width, 0, p0[2] + pz * width)
        v1 = (p0[0] - px * width, 0, p0[2] - pz * width)
        v2 = (p1[0] - px * width, 0, p1[2] - pz * width)
        v3 = (p1[0] + px * width, 0, p1[2] + pz * width)

        base_index = len(verts)
        verts.extend([v0, v1, v2, v3])

        faces.append([base_index, base_index+1, base_index+2, base_index+3])

    mesh = cmds.polyCreateFacet(p=verts[:4])[0]

    for f in faces[1:]:
        cmds.polyAppend(mesh, a=f)

    return mesh

# Create or update the semi-transparent fill material for one control.
def create_control_fill_material(name, color):
    shader_name = name + "_fill_mat"
    sg_name = shader_name + "SG"

    if not cmds.pluginInfo("mtoa", query=True, loaded=True):
        cmds.loadPlugin("mtoa")

    if not cmds.objExists(shader_name):
        shader = cmds.shadingNode("aiStandardSurface", asShader=True, name=shader_name)
        cmds.setAttr(shader + ".base", 1.0)
        cmds.setAttr(shader + ".baseColor", *color, type="double3")
        cmds.setAttr(shader + ".specular", 0.0)
        cmds.setAttr(
            shader + ".opacity",
            CONTROL_OPACITY,
            CONTROL_OPACITY,
            CONTROL_OPACITY,
            type="double3",
        )
    else:
        shader = shader_name
        cmds.setAttr(shader + ".base", 1.0)
        cmds.setAttr(shader + ".baseColor", *color, type="double3")
        cmds.setAttr(shader + ".specular", 0.0)
        cmds.setAttr(
            shader + ".opacity",
            CONTROL_OPACITY,
            CONTROL_OPACITY,
            CONTROL_OPACITY,
            type="double3",
        )

    if not cmds.objExists(sg_name):
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg_name)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    else:
        sg = sg_name
        connections = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
        if shader not in connections:
            cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)

    return shader, sg

# -------------------------
# Slice Rotation
# -------------------------
# Rotate one cube slice identified by axis/layer value.
def rotate_slice(axis="y", value=1, angle=90, animate=True, start_frame=None):
    ensure_initial_state()
    cubies = get_cubies_on_axis(axis, value)
    if not cubies:
        print("No cubies found on the requested slice")
        return False

    rotate_cubies(
        cubies,
        axis,
        angle,
        animate=animate,
        start_frame=start_frame,
        clear_future=animate,
    )
    return True


# -------------------------
# Move Definitions And Operations
# -------------------------
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
PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT = 6
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

INVERSE_MOVES = {
    move_name: get_inverse_move(move_name)
    for move_name in SEARCH_MOVE_NAMES
}

def build_solver_piece_metadata():
    return build_shared_solver_piece_metadata()

# Build the immutable logical solved piece states used to seed the catalogs.
def build_solved_piece_states():
    return build_shared_solved_piece_states(SOLVER_PIECES)

# Enumerate every reachable state code for each individual piece.
def build_piece_state_catalogs():
    return build_shared_piece_state_catalogs(SOLVED_PIECE_STATES)

# Precompute how every solver move transforms every encoded piece state.
def build_move_state_tables():
    return build_shared_move_state_tables(PIECE_STATE_INFOS, PIECE_STATE_TO_CODE)

# Build the immutable encoded solved state used as the search target.
def build_solved_cube_state():
    return build_shared_solved_cube_state(PIECE_STATE_TO_CODE, SOLVED_PIECE_STATES)

# Return which axis a logical direction vector lies on.
def get_vector_axis_index(vector):
    if vector[0] != 0:
        return 0
    if vector[1] != 0:
        return 1
    return 2

# Return the corner-orientation value for one encoded corner piece state.
def get_corner_orientation_from_piece_state(piece_state):
    position = piece_state[0]
    ud_direction = piece_state[1]
    if get_vector_axis_index(ud_direction) == 1:
        return 0

    if position[0] * position[1] * position[2] == 1:
        return 1 if ud_direction == (position[0], 0, 0) else 2

    return 1 if ud_direction == (0, 0, position[2]) else 2

# Return whether a move is an F/B quarter turn that flips moved edges.
def is_edge_flipping_move(move_name):
    return move_name[0] in ("F", "B") and not move_name.endswith("2")

# Rank a permutation tuple into the range [0, n!).
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

# Decode a permutation rank back into a tuple of values [0, length).
def unrank_permutation(rank, length):
    remaining_values = list(range(length))
    permutation = []

    for remaining_count in range(length, 0, -1):
        factorial = FACTORIALS[remaining_count - 1]
        value_index = rank // factorial
        rank %= factorial
        permutation.append(remaining_values.pop(value_index))

    return tuple(permutation)

# Encode the first seven corner twists into a compact base-3 index.
def encode_corner_orientation(corner_orientation):
    index = 0
    for orientation in corner_orientation[:7]:
        index = index * 3 + orientation
    return index

# Decode a compact corner-orientation index into eight twist values.
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

# Encode the first eleven edge flips into a compact bit-packed index.
def encode_edge_orientation(edge_orientation):
    index = 0
    for orientation in edge_orientation[:11]:
        index = (index << 1) | orientation
    return index

# Decode a compact edge-orientation index into twelve flip values.
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

# Build the metadata needed to convert between full cube states and cubie coordinates.
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
    for piece_id, piece_index in enumerate(corner_piece_indices):
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
    for piece_id, piece_index in enumerate(edge_piece_indices):
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
                            piece_index,
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

# Build one full logical cube state from cubie permutation/orientation coordinates.
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

# Extract the compact corner-orientation index from a full cube state.
def extract_corner_orientation_index_from_state(state):
    corner_orientation = [0] * 8

    for piece_id, piece_index in enumerate(CORNER_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = CORNER_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        corner_orientation[position_index] = get_corner_orientation_from_piece_state(piece_state)

    return encode_corner_orientation(corner_orientation)

# Extract the compact edge-orientation index from a full cube state.
def extract_edge_orientation_index_from_state(state):
    edge_orientation = [0] * 12

    for piece_id, piece_index in enumerate(EDGE_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = EDGE_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        edge_orientation[position_index] = EDGE_ORIENTATION_VALUES[piece_id][state[piece_index]]

    return encode_edge_orientation(edge_orientation)

# Extract which four positions currently contain the middle-slice edges.
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

# Extract the compact corner-permutation index from a phase-2-compatible state.
def extract_corner_permutation_index_from_state(state):
    corner_permutation = [0] * 8

    for piece_id, piece_index in enumerate(CORNER_PIECE_INDICES):
        piece_state = PIECE_STATE_INFOS[piece_index][state[piece_index]]
        position_index = CORNER_POSITION_INDEX_BY_VECTOR[piece_state[0]]
        corner_permutation[position_index] = piece_id

    return rank_permutation(corner_permutation)

# Extract the compact U/D-edge permutation index from a phase-2-compatible state.
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

# Extract the compact slice-edge permutation index from a phase-2-compatible state.
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

# Build a representative full cube state for one corner-orientation coordinate.
def build_corner_orientation_state(state_index):
    return build_cube_state_from_coordinates(
        corner_orientation=decode_corner_orientation(state_index),
    )

# Build a representative full cube state for one edge-orientation coordinate.
def build_edge_orientation_state(state_index):
    return build_cube_state_from_coordinates(
        edge_orientation=decode_edge_orientation(state_index),
    )

# Build a representative full cube state for one slice-position coordinate.
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

# Build a representative full cube state for one corner-permutation coordinate.
def build_corner_permutation_state(state_index):
    return build_cube_state_from_coordinates(
        corner_permutation=unrank_permutation(state_index, 8),
    )

# Build a representative full cube state for one U/D-edge permutation coordinate.
def build_edge_permutation_state(state_index):
    compact_permutation = unrank_permutation(state_index, 8)
    edge_permutation = list(SOLVED_EDGE_PERMUTATION)

    for offset, position_index in enumerate(PHASE2_UD_EDGE_POSITIONS):
        edge_permutation[position_index] = PHASE2_UD_EDGE_PIECES[compact_permutation[offset]]

    return build_cube_state_from_coordinates(edge_permutation=tuple(edge_permutation))

# Build a representative full cube state for one slice-edge permutation coordinate.
def build_slice_permutation_state(state_index):
    compact_permutation = unrank_permutation(state_index, 4)
    edge_permutation = list(SOLVED_EDGE_PERMUTATION)

    for offset, position_index in enumerate(SLICE_EDGE_POSITIONS):
        edge_permutation[position_index] = SLICE_EDGE_PIECES[compact_permutation[offset]]

    return build_cube_state_from_coordinates(edge_permutation=tuple(edge_permutation))

# Build one flat move table for a single coordinate family.
def build_coordinate_move_table(
    state_count,
    move_names,
    build_state_function,
    extract_index_function,
    label,
):
    print("Building {0} move table...".format(label))
    move_table = array("H", [0]) * (state_count * len(move_names))

    for state_index in range(state_count):
        representative_state = build_state_function(state_index)
        table_offset = state_index * len(move_names)

        for move_offset, move_name in enumerate(move_names):
            next_state = apply_move_to_cube_state(representative_state, move_name)
            move_table[table_offset + move_offset] = extract_index_function(next_state)

        if (state_index + 1) % SOLVER_PROGRESS_STEP == 0:
            process_ui_events()

    return move_table

# Build a BFS pruning table over a pair of coordinates that share the same move set.
def build_combined_pruning_table(
    primary_move_table,
    secondary_move_table,
    secondary_size,
    move_count,
    label,
    primary_start_index=0,
    secondary_start_index=0,
):
    print("Building {0} pruning table...".format(label))
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
            process_ui_events()

    return pruning_table

# Lazily build the move and pruning tables used by the two-phase solver.
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
    global PHASE1_REVERSE_FRONTIER_DEPTHS

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
        and PHASE1_REVERSE_FRONTIER_DEPTHS
    ):
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
    print(
        "Two-phase solver tables are ready after {0:.2f} seconds".format(
            time.perf_counter() - build_start,
        )
    )

# Return whether the saved solve target is still the canonical solved cube.
def is_standard_solver_goal():
    ensure_solver_state()
    return SOLVED_CUBE_STATE == CANONICAL_SOLVED_CUBE_STATE

# Estimate a lower bound for phase 1 from the current coordinate triple.
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

# Estimate a lower bound for phase 2 from the current coordinate triple.
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

# Build a reverse BFS frontier for phase 1 so the forward search only needs to
# cover the unmatched half of the distance.
def build_phase1_reverse_frontier():
    global PHASE1_REVERSE_FRONTIER_DEPTHS

    if PHASE1_REVERSE_FRONTIER_DEPTHS:
        return

    print(
        "Building phase-1 reverse frontier to depth {0}...".format(
            PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT,
        )
    )
    start_coordinates = (0, 0, SOLVED_SLICE_POSITION_INDEX)
    start_key = get_phase1_coordinate_key(*start_coordinates)
    frontier_depths = {start_key: 0}
    queue = [(start_coordinates, 0)]
    queue_index = 0

    while queue_index < len(queue):
        (corner_orientation_index, edge_orientation_index, slice_position_index), depth = queue[queue_index]
        queue_index += 1
        if depth >= PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT:
            continue

        corner_offset = corner_orientation_index * PHASE1_MOVE_COUNT
        edge_offset = edge_orientation_index * PHASE1_MOVE_COUNT
        slice_offset = slice_position_index * PHASE1_MOVE_COUNT
        next_depth = depth + 1

        for move_offset in range(PHASE1_MOVE_COUNT):
            next_coordinates = (
                PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset],
                PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset],
                PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset],
            )
            next_key = get_phase1_coordinate_key(*next_coordinates)
            if next_key in frontier_depths:
                continue

            frontier_depths[next_key] = next_depth
            queue.append((next_coordinates, next_depth))

        if queue_index % SOLVER_PROGRESS_STEP == 0:
            process_ui_events()

    PHASE1_REVERSE_FRONTIER_DEPTHS = frontier_depths

# Reconstruct one phase-1 suffix from a frontier state back to the G1 goal.
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
    current_depth = PHASE1_REVERSE_FRONTIER_DEPTHS.get(
        get_phase1_coordinate_key(*current_coordinates)
    )
    if current_depth is None:
        return None

    while current_depth > 0:
        corner_offset = current_coordinates[0] * PHASE1_MOVE_COUNT
        edge_offset = current_coordinates[1] * PHASE1_MOVE_COUNT
        slice_offset = current_coordinates[2] * PHASE1_MOVE_COUNT
        found_next = False

        for move_offset, move_name in enumerate(PHASE1_MOVE_NAMES):
            next_coordinates = (
                PHASE1_CORNER_ORIENTATION_MOVE_TABLE[corner_offset + move_offset],
                PHASE1_EDGE_ORIENTATION_MOVE_TABLE[edge_offset + move_offset],
                PHASE1_SLICE_POSITION_MOVE_TABLE[slice_offset + move_offset],
            )
            next_depth = PHASE1_REVERSE_FRONTIER_DEPTHS.get(
                get_phase1_coordinate_key(*next_coordinates)
            )
            if next_depth != current_depth - 1:
                continue

            suffix.append(move_name)
            current_coordinates = next_coordinates
            current_depth = next_depth
            found_next = True
            break

        if not found_next:
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

# Return the combined number of nodes searched across both solver phases.
def get_two_phase_node_count(stats):
    return stats["phase1_nodes"] + stats["phase2_nodes"]

# Return the face letter for the last move, or None before the search starts.
def get_search_context_face(last_move):
    if not last_move:
        return None
    return get_move_face(last_move)

# Apply a move sequence to a logical cube state and return the resulting state.
def apply_move_sequence_to_cube_state(state, move_names):
    return apply_move_sequence_with_tables(state, MOVE_STATE_TABLES, move_names)

# Run one bounded phase-2 depth-first pass after orientation and slice placement are solved.
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

    if (
        corner_permutation_index == 0
        and edge_permutation_index == 0
        and slice_permutation_index == 0
    ):
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
        process_ui_events()

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

# Search phase 2 from one phase-1 goal node using only the remaining total-depth budget.
def find_phase2_solution(start_state, stats, deadline, max_nodes, max_depth, last_move=None):
    corner_permutation_index = extract_corner_permutation_index_from_state(start_state)
    edge_permutation_index = extract_edge_permutation_index_from_state(start_state)
    slice_permutation_index = extract_slice_permutation_index_from_state(start_state)
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

# Try to finish phase 1 by joining the current coordinates against the reverse frontier.
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
    reverse_depth = PHASE1_REVERSE_FRONTIER_DEPTHS.get(
        get_phase1_coordinate_key(
            corner_orientation_index,
            edge_orientation_index,
            slice_position_index,
        )
    )
    if reverse_depth is None or depth + reverse_depth > phase1_depth_limit:
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

# Run one bounded phase-1 pass, using the reverse frontier as an exact midpoint join.
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
        process_ui_events()

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

        next_reverse_depth = PHASE1_REVERSE_FRONTIER_DEPTHS.get(
            get_phase1_coordinate_key(
                next_corner_orientation_index,
                next_edge_orientation_index,
                next_slice_position_index,
            )
        )
        ordered_moves.append(
            (
                99 if next_reverse_depth is None else next_reverse_depth,
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

# Solve the canonical cube state with a Kociemba-style two-phase IDA* search.
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

    for total_bound in range(minimum_phase1_bound, maximum_total_bound + 1):
        stats["searched_total_bound"] = total_bound
        stats["searched_phase1_bound"] = min(total_bound, TWO_PHASE_MAX_PHASE1_DEPTH)
        forward_depth_limit = max(
            0,
            stats["searched_phase1_bound"] - PHASE1_REVERSE_FRONTIER_DEPTH_LIMIT,
        )
        result = search_phase1_with_ida_star(
            start_state,
            corner_orientation_index,
            edge_orientation_index,
            slice_position_index,
            depth=0,
            forward_depth_limit=forward_depth_limit,
            phase1_depth_limit=stats["searched_phase1_bound"],
            total_bound=total_bound,
            path=[],
            last_move=None,
            stats=stats,
            deadline=deadline,
            max_nodes=max_nodes,
            best_depths={},
        )

        if result["solution"] is not None:
            return result["solution"], stats

        if result["hit_limit"]:
            stats["hit_limit"] = True
            stats["limit_type"] = result["limit_type"]
            return None, stats

    stats["hit_limit"] = True
    stats["limit_type"] = "total_depth"
    return None, stats

# Reset the logical cube-state model to the canonical solved state.
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

# Ensure the logical solver state exists before moves or searches run.
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

# Promote the current logical state to the saved reset/goal state.
def save_solver_goal_from_current_state():
    global SOLVED_CUBE_STATE

    ensure_solver_state()
    SOLVED_CUBE_STATE = CUBE_STATE

# Apply one search move to the abstract cube-state model.
def apply_move_to_cube_state(state, move_name):
    return apply_move_to_cube_state_with_tables(state, MOVE_STATE_TABLES, move_name)

# Return whether a logical state already matches the saved goal state.
def is_cube_state_solved(state=None):
    ensure_solver_state()
    if state is None:
        state = CUBE_STATE
    return state == SOLVED_CUBE_STATE

# Estimate a lower bound for how many solver moves remain.
def estimate_remaining_moves(state, goal_state=None):
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

# Run one IDA* depth-first pass and return either a solution or the next bound.
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
        process_ui_events()

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

# Run a bounded iterative-deepening A* search over the logical cube state.
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

# Generate a random scramble while avoiding immediate same-face repeats.
def generate_scramble(length=SCRAMBLE_LENGTH):
    return generate_scramble_text(length, move_names=MOVES.keys(), rng=random)


# Temporarily suspend Cached Playback so animated scrambles do not fill Maya's
# evaluation cache with every intermediate turn.
def suspend_cached_playback_for_scramble():
    global SCRAMBLE_CACHE_WAS_ENABLED

    if SCRAMBLE_CACHE_WAS_ENABLED is not None:
        return

    try:
        cache_enabled = cmds.evaluator(name="cache", query=True, enable=True)
    except Exception:
        SCRAMBLE_CACHE_WAS_ENABLED = None
        return

    SCRAMBLE_CACHE_WAS_ENABLED = bool(cache_enabled)
    if not SCRAMBLE_CACHE_WAS_ENABLED:
        return

    try:
        cmds.evaluator(name="cache", enable=False)
        cmds.cacheEvaluator(flushCache="destroy")
    except Exception:
        pass


# Restore Cached Playback to the user's previous state after scrambling.
def restore_cached_playback_after_scramble():
    global SCRAMBLE_CACHE_WAS_ENABLED

    if SCRAMBLE_CACHE_WAS_ENABLED is None:
        return

    was_enabled = SCRAMBLE_CACHE_WAS_ENABLED
    SCRAMBLE_CACHE_WAS_ENABLED = None

    if not was_enabled:
        return

    try:
        cmds.evaluator(name="cache", enable=True)
    except Exception:
        pass


# Temporarily suspend Cached Playback so animated solve/algorithm playback does
# not accumulate every intermediate keyed turn in the evaluation cache.
def suspend_cached_playback_for_algorithm():
    global ALGORITHM_CACHE_WAS_ENABLED

    if ALGORITHM_CACHE_WAS_ENABLED is not None:
        return

    try:
        cache_enabled = cmds.evaluator(name="cache", query=True, enable=True)
    except Exception:
        ALGORITHM_CACHE_WAS_ENABLED = None
        return

    ALGORITHM_CACHE_WAS_ENABLED = bool(cache_enabled)
    if not ALGORITHM_CACHE_WAS_ENABLED:
        return

    try:
        cmds.evaluator(name="cache", enable=False)
        cmds.cacheEvaluator(flushCache="destroy")
    except Exception:
        pass


# Restore Cached Playback to the user's previous state after a deferred
# algorithm or solve run completes.
def restore_cached_playback_after_algorithm():
    global ALGORITHM_CACHE_WAS_ENABLED

    if ALGORITHM_CACHE_WAS_ENABLED is None:
        return

    was_enabled = ALGORITHM_CACHE_WAS_ENABLED
    ALGORITHM_CACHE_WAS_ENABLED = None

    if not was_enabled:
        return

    try:
        cmds.evaluator(name="cache", enable=True)
    except Exception:
        pass


# Apply a single move from a UI button or viewport control.
def move(move_name):
    if is_busy_with_sequence():
        cmds.warning("Stop the active sequence before making manual moves")
        return
    apply_move(move_name)


# Rotate the entire cube and refresh orientation-dependent UI state.
def rotate_cube(rotation_name):
    if rotation_name not in CUBE_ROTATIONS:
        print("Invalid cube rotation")
        return

    axis, angle = CUBE_ROTATIONS[rotation_name]
    rotate_cubies(
        get_all_cubies() + get_viewport_controls(),
        axis,
        angle,
        animate=get_animation_enabled(),
        clear_future=get_animation_enabled(),
    )
    update_current_orientation(axis, angle)
    update_viewport_control_names_by_position()
    update_ui_move_buttons()


# Clear animation, restore the saved reset pose, and reset history.
def clear_animation_and_reset(*_unused):
    global current_time
    global CURRENT_ORIENTATION
    global CUBE_STATE
    global MOVE_HISTORY
    global MOVE_HISTORY_BASE_FRAME
    global MOVE_HISTORY_INDEX
    global VISIBLE_HISTORY_LENGTH

    controls_enabled = cmds.objExists("rubik_controls_grp")

    ensure_initial_state()
    cubies = [cubie for cubie in INITIAL_STATE if cmds.objExists(cubie)]
    if not cubies:
        print("No saved initial state found")
        return

    clear_transform_keys(cubies)
    cmds.currentTime(1)

    for cubie in cubies:
        cmds.xform(cubie, ws=True, matrix=INITIAL_STATE[cubie])

    if INITIAL_ORIENTATION:
        CURRENT_ORIENTATION = {
            axis: vector[:]
            for axis, vector in INITIAL_ORIENTATION.items()
        }
    else:
        reset_orientation()

    MOVE_HISTORY.clear()
    MOVE_HISTORY_FRAMES.clear()
    MOVE_HISTORY_BASE_FRAME = 1
    MOVE_HISTORY_INDEX = 0
    VISIBLE_HISTORY_LENGTH = 0
    ensure_solver_state()
    CUBE_STATE = SOLVED_CUBE_STATE

    if controls_enabled:
        setup_viewport_controls()
    else:
        reset_viewport_control_directions()

    update_ui_move_buttons()
    update_playback_ui()

    sync_timeline_to_history_index(0)

# Reverse a move sequence by reversing order and inverting each move.
def reverse_sequence(sequence):
    try:
        move_names = parse_move_sequence(sequence)
    except ValueError:
        move_names = normalize_algorithm_text(sequence).split()
    reverse = [INVERSE_MOVES.get(move_name, move_name) for move_name in reversed(move_names)]
    return format_move_sequence(reverse)


def get_algorithm_field_text():
    if not cmds.control(ALGORITHM_FIELD, exists=True):
        return ""
    return cmds.scrollField(ALGORITHM_FIELD, q=True, text=True)


def set_algorithm_field_text(sequence):
    if not cmds.control(ALGORITHM_FIELD, exists=True):
        return

    cmds.scrollField(
        ALGORITHM_FIELD,
        e=True,
        text=normalize_algorithm_text(sequence),
    )


def begin_algorithm_run(move_names):
    global ALGORITHM_RUN_ACTIVE
    global ALGORITHM_RUN_PENDING_MOVES
    global ALGORITHM_RUN_STOP_REQUESTED

    normalized_sequence = format_move_sequence(move_names)
    set_algorithm_field_text(normalized_sequence)
    ALGORITHM_RUN_ACTIVE = True
    ALGORITHM_RUN_STOP_REQUESTED = False
    ALGORITHM_RUN_PENDING_MOVES = list(move_names)
    if get_animation_enabled():
        suspend_cached_playback_for_algorithm()
    update_run_algorithm_button_label()
    update_playback_ui()
    schedule_next_algorithm_move()


def load_move_history_into_algorithm_field(*_unused):
    set_algorithm_field_text(format_move_sequence(MOVE_HISTORY[:MOVE_HISTORY_INDEX]))


def load_inverse_history_into_algorithm_field(*_unused):
    set_algorithm_field_text(reverse_sequence(format_move_sequence(MOVE_HISTORY[:MOVE_HISTORY_INDEX])))


def run_algorithm_from_field(*_unused):
    if PLAYBACK_ACTIVE:
        cmds.warning("Pause playback before running an algorithm")
        return

    if SCRAMBLE_ACTIVE:
        cmds.warning("Stop the scramble before running an algorithm")
        return

    if ALGORITHM_RUN_ACTIVE:
        # Match scramble behavior: do not interrupt the active turn, just stop
        # before scheduling the next one.
        ALGORITHM_RUN_STOP_REQUESTED = True
        update_run_algorithm_button_label()
        return

    sequence = get_algorithm_field_text()
    if not sequence.strip():
        cmds.warning("Enter an algorithm to run")
        return

    try:
        move_names = parse_move_sequence(sequence, valid_moves=MOVES)
    except ValueError as error:
        cmds.warning(str(error))
        return

    begin_algorithm_run(move_names)


def schedule_next_algorithm_move():
    if QtCore is not None:
        QtCore.QTimer.singleShot(0, process_next_algorithm_move)
        return

    maya_utils.executeDeferred(process_next_algorithm_move)


def finish_algorithm_run():
    global ALGORITHM_RUN_ACTIVE
    global ALGORITHM_RUN_PENDING_MOVES
    global ALGORITHM_RUN_STOP_REQUESTED

    ALGORITHM_RUN_ACTIVE = False
    ALGORITHM_RUN_STOP_REQUESTED = False
    ALGORITHM_RUN_PENDING_MOVES = []
    restore_cached_playback_after_algorithm()
    update_run_algorithm_button_label()
    update_playback_ui()


def process_next_algorithm_move():
    global ALGORITHM_RUN_PENDING_MOVES

    if not ALGORITHM_RUN_ACTIVE:
        return

    if ALGORITHM_RUN_STOP_REQUESTED or not ALGORITHM_RUN_PENDING_MOVES:
        finish_algorithm_run()
        return

    # Execute exactly one move, then yield back to Maya so the same button can
    # request a clean stop between turns instead of interrupting mid-turn.
    move_name = ALGORITHM_RUN_PENDING_MOVES.pop(0)
    apply_move(move_name)

    if ALGORITHM_RUN_STOP_REQUESTED or not ALGORITHM_RUN_PENDING_MOVES:
        finish_algorithm_run()
        return

    schedule_next_algorithm_move()


def update_playback_ui():
    global PLAYBACK_SCRUB_UPDATING

    update_ui_move_buttons()

    if cmds.control(PLAYBACK_PLAY_BUTTON, exists=True):
        play_label = "Pause Playback" if PLAYBACK_ACTIVE else "Play Forward"
        cmds.button(PLAYBACK_PLAY_BUTTON, e=True, label=play_label)

    can_back = can_step_backward() and not is_busy_with_sequence()
    can_forward = can_step_forward() and not is_busy_with_sequence()
    can_toggle_playback = (can_step_forward() or PLAYBACK_ACTIVE) and not SCRAMBLE_ACTIVE and not ALGORITHM_RUN_ACTIVE

    for control_name in (PLAYBACK_STEP_BACK_BUTTON, PLAYBACK_UNDO_BUTTON):
        if cmds.control(control_name, exists=True):
            cmds.button(control_name, e=True, enable=can_back)

    for control_name in (PLAYBACK_STEP_FORWARD_BUTTON, PLAYBACK_REDO_BUTTON):
        if cmds.control(control_name, exists=True):
            cmds.button(control_name, e=True, enable=can_forward)

    if cmds.control(PLAYBACK_PLAY_BUTTON, exists=True):
        cmds.button(PLAYBACK_PLAY_BUTTON, e=True, enable=can_toggle_playback)

    if cmds.control(PLAYBACK_SCRUB_SLIDER, exists=True):
        PLAYBACK_SCRUB_UPDATING = True
        try:
            cmds.intSliderGrp(
                PLAYBACK_SCRUB_SLIDER,
                e=True,
                min=0,
                max=max(1, VISIBLE_HISTORY_LENGTH),
                fieldMinValue=0,
                fieldMaxValue=max(1, VISIBLE_HISTORY_LENGTH),
                value=MOVE_HISTORY_INDEX,
                enable=not is_busy_with_sequence(),
            )
        finally:
            PLAYBACK_SCRUB_UPDATING = False

    if cmds.control(PLAYBACK_STATUS_TEXT, exists=True):
        if VISIBLE_HISTORY_LENGTH:
            status = "History Position: {0}/{1}".format(MOVE_HISTORY_INDEX, VISIBLE_HISTORY_LENGTH)
        else:
            status = "History Position: 0/0"
        cmds.text(PLAYBACK_STATUS_TEXT, e=True, label=status)


def finish_playback():
    global PLAYBACK_ACTIVE
    global PLAYBACK_STOP_REQUESTED

    PLAYBACK_ACTIVE = False
    PLAYBACK_STOP_REQUESTED = False
    update_playback_ui()


def schedule_next_playback_step():
    if QtCore is not None:
        QtCore.QTimer.singleShot(0, process_next_playback_step)
        return

    maya_utils.executeDeferred(process_next_playback_step)


def step_history_forward(animate=None, start_frame=None):
    global MOVE_HISTORY_INDEX

    if not can_step_forward():
        return False

    if animate is False and can_use_keyed_history_scrub():
        target_index = MOVE_HISTORY_INDEX + 1
        sync_logical_state_to_history_index(target_index)
        sync_timeline_to_history_index(target_index)
        update_playback_ui()
        return True

    if animate is False:
        return rebuild_scene_to_history_index(MOVE_HISTORY_INDEX + 1)

    move_name = MOVE_HISTORY[MOVE_HISTORY_INDEX]
    if not apply_move(move_name, animate=animate, start_frame=start_frame, track_history=False):
        return False

    MOVE_HISTORY_INDEX += 1
    if animate:
        MOVE_HISTORY_FRAMES[MOVE_HISTORY_INDEX - 1] = current_time
    else:
        sync_timeline_to_history_index()
    update_playback_ui()
    return True


def step_history_backward(animate=None, start_frame=None):
    global MOVE_HISTORY_INDEX

    if not can_step_backward():
        return False

    if animate is False and can_use_keyed_history_scrub():
        target_index = MOVE_HISTORY_INDEX - 1
        sync_logical_state_to_history_index(target_index)
        sync_timeline_to_history_index(target_index)
        update_playback_ui()
        return True

    if animate is False:
        return rebuild_scene_to_history_index(MOVE_HISTORY_INDEX - 1)

    if MOVE_HISTORY_INDEX == 1:
        move_name = MOVE_HISTORY[0]
        inverse_move = INVERSE_MOVES.get(move_name, move_name)
        if animate:
            if not apply_move(
                inverse_move,
                animate=True,
                start_frame=start_frame,
                track_history=False,
            ):
                return False

        if not restore_cube_to_history_base_state():
            return False

        MOVE_HISTORY_INDEX = 0
        sync_timeline_to_history_index()
        update_playback_ui()
        return True

    move_name = MOVE_HISTORY[MOVE_HISTORY_INDEX - 1]
    inverse_move = INVERSE_MOVES.get(move_name, move_name)
    if not apply_move(inverse_move, animate=animate, start_frame=start_frame, track_history=False):
        return False

    MOVE_HISTORY_INDEX -= 1
    if animate and MOVE_HISTORY_INDEX > 0:
        MOVE_HISTORY_FRAMES[MOVE_HISTORY_INDEX - 1] = current_time
    else:
        sync_timeline_to_history_index()
    update_playback_ui()
    return True


def process_next_playback_step():
    global current_time

    if not PLAYBACK_ACTIVE:
        return

    if PLAYBACK_STOP_REQUESTED or not can_step_forward():
        finish_playback()
        return

    start_frame = max(get_current_frame(), current_time) if get_animation_enabled() else None
    if not step_history_forward(animate=get_animation_enabled(), start_frame=start_frame):
        finish_playback()
        return

    if PLAYBACK_STOP_REQUESTED or not can_step_forward():
        finish_playback()
        return

    schedule_next_playback_step()


def toggle_playback(*_unused):
    global PLAYBACK_ACTIVE
    global PLAYBACK_STOP_REQUESTED

    if SCRAMBLE_ACTIVE:
        cmds.warning("Stop the scramble before using playback")
        return

    if ALGORITHM_RUN_ACTIVE:
        cmds.warning("Stop the algorithm before using playback")
        return

    if PLAYBACK_ACTIVE:
        PLAYBACK_STOP_REQUESTED = True
        update_playback_ui()
        return

    if not can_step_forward():
        cmds.warning("Move the playback position backward before playing forward")
        return

    PLAYBACK_ACTIVE = True
    PLAYBACK_STOP_REQUESTED = False
    update_playback_ui()
    schedule_next_playback_step()


def undo_move(*_unused):
    global VISIBLE_HISTORY_LENGTH

    if is_busy_with_sequence():
        cmds.warning("Stop the active sequence before undoing moves")
        return

    if not step_history_backward(animate=get_animation_enabled()):
        cmds.warning("No moves available to undo")
        return

    VISIBLE_HISTORY_LENGTH = MOVE_HISTORY_INDEX
    update_playback_ui()


def redo_move(*_unused):
    global VISIBLE_HISTORY_LENGTH

    if is_busy_with_sequence():
        cmds.warning("Stop the active sequence before redoing moves")
        return

    if not step_history_forward(animate=get_animation_enabled()):
        cmds.warning("No moves available to redo")
        return

    VISIBLE_HISTORY_LENGTH = max(VISIBLE_HISTORY_LENGTH, MOVE_HISTORY_INDEX)
    update_playback_ui()


def scrub_to_history_position(value):
    if PLAYBACK_SCRUB_UPDATING:
        return

    if is_busy_with_sequence():
        return

    target_index = max(0, min(int(value), VISIBLE_HISTORY_LENGTH))
    while MOVE_HISTORY_INDEX < target_index:
        if not step_history_forward(animate=False):
            break

    while MOVE_HISTORY_INDEX > target_index:
        if not step_history_backward(animate=False):
            break
    
# Solve the current logical cube state with the strongest available solver path.
def solve_from_history(*_unused):
    global MOVE_HISTORY_BASE_FRAME
    global MOVE_HISTORY_INDEX
    global VISIBLE_HISTORY_LENGTH

    if not get_all_cubies():
        cmds.warning("Build the cube before solving")
        return

    ensure_solver_state()
    if is_cube_state_solved():
        cmds.warning("Cube is already solved")
        return

    solver_core = get_standalone_solver_core()
    can_use_two_phase = is_standard_solver_goal()
    if can_use_two_phase:
        solver_label = "embedded two-phase solver"
        two_phase_search = find_two_phase_solution
        if solver_core is None and STANDALONE_SOLVER_IMPORT_ERROR is not None:
            cmds.warning(
                "Standalone solver core could not be loaded ({0}). Using the embedded two-phase solver instead.".format(
                    STANDALONE_SOLVER_IMPORT_ERROR,
                )
            )
        if solver_core is not None:
            try:
                external_goal_state = solver_core.get_canonical_solved_state()
                if CANONICAL_SOLVED_CUBE_STATE and external_goal_state != CANONICAL_SOLVED_CUBE_STATE:
                    raise ValueError("standalone solver state encoding does not match the embedded solver state")

                solver_label = "standalone solver core"
                two_phase_search = lambda state: solver_core.find_two_phase_solution(
                    state,
                    max_nodes=SOLVER_MAX_NODES,
                )
            except Exception as error:
                cmds.warning(
                    "Standalone solver core could not be prepared ({0}). Falling back to the embedded two-phase solver.".format(
                        error,
                    )
                )

        print(
            "Searching with a two-phase IDA* solver and pruning tables via the {0} "
            "(phase 1 <= {1}, phase 2 <= {2}, nodes <= {3}, {5} seconds <= {4})...".format(
                solver_label,
                TWO_PHASE_MAX_PHASE1_DEPTH,
                TWO_PHASE_MAX_PHASE2_DEPTH,
                SOLVER_MAX_NODES,
                SOLVER_MAX_SECONDS,
                SOLVER_TIME_SOURCE_LABEL.lower(),
            )
        )

        try:
            solution_moves, search_stats = two_phase_search(CUBE_STATE)
        except Exception as error:
            cmds.warning(
                "{0} failed to initialize cleanly ({1}). Falling back to the existing IDA* search.".format(
                    solver_label.capitalize(),
                    error,
                )
            )
        else:
            if solution_moves is not None:
                execution_moves = expand_solver_moves(solution_moves)
                solution = " ".join(execution_moves)
                print(
                    "Two-phase IDA* found a {0}-move solution ({1} quarter turns) "
                    "after exploring {2} phase-1 nodes and {3} phase-2 nodes".format(
                        len(solution_moves),
                        len(execution_moves),
                        search_stats["phase1_nodes"],
                        search_stats["phase2_nodes"],
                    )
                )
                if solution:
                    # Preserve the existing move history so playback can scrub
                    # across the full session, including the solve itself, and
                    # run it through the deferred scheduler so playback UI can
                    # update live during the solve.
                    begin_algorithm_run(execution_moves)
                return

            if search_stats["limit_type"] == "time":
                limit_message = (
                    "Two-phase IDA* hit its current {0} time budget after exploring {1} total states "
                    "(phase 1: {2}, phase 2: {3}).".format(
                        SOLVER_TIME_SOURCE_LABEL.lower(),
                        get_two_phase_node_count(search_stats),
                        search_stats["phase1_nodes"],
                        search_stats["phase2_nodes"],
                    )
                )
            elif search_stats["limit_type"] == "nodes":
                limit_message = (
                    "Two-phase IDA* hit its current node budget after exploring {0} total states "
                    "(phase 1: {1}, phase 2: {2}).".format(
                        get_two_phase_node_count(search_stats),
                        search_stats["phase1_nodes"],
                        search_stats["phase2_nodes"],
                    )
                )
            else:
                limit_message = (
                    "Two-phase IDA* found no solution within the current total depth bound of {0} "
                    "(phase 1 <= {1}, phase 2 <= {2}) after exploring {3} total states.".format(
                        search_stats["searched_total_bound"],
                        search_stats["searched_phase1_bound"],
                        search_stats["searched_phase2_bound"],
                        get_two_phase_node_count(search_stats),
                    )
                )

            cmds.warning(limit_message)
            return

        print(
            "Falling back to the existing single-phase IDA* search after the two-phase solver error."
        )

    else:
        print(
            "The current solve target is a custom saved pose, so the canonical two-phase "
            "solver is falling back to the existing single-phase IDA* search."
        )

    solution_moves, search_stats = find_basic_a_star_solution(CUBE_STATE)
    if solution_moves is not None:
        execution_moves = expand_solver_moves(solution_moves)
        solution = " ".join(execution_moves)
        print(
            "Fallback IDA* found a {0}-move solution ({1} quarter turns) after exploring {2} states".format(
                len(solution_moves),
                len(execution_moves),
                search_stats["expanded_nodes"],
            )
        )
        if solution:
            # Keep the pre-solve move record available in playback and append
            # the solver moves after it while letting the scrubber update live.
            begin_algorithm_run(execution_moves)
        return

    if search_stats["limit_type"] == "time":
        limit_message = (
            "Fallback IDA* hit its current {0} time budget after exploring {1} states "
            "and reaching depth {2}.".format(
                SOLVER_TIME_SOURCE_LABEL.lower(),
                search_stats["expanded_nodes"],
                search_stats["deepest_depth"],
            )
        )
    elif search_stats["limit_type"] == "nodes":
        limit_message = (
            "Fallback IDA* hit its current node budget after exploring {0} states "
            "and reaching depth {1}.".format(
                search_stats["expanded_nodes"],
                search_stats["deepest_depth"],
            )
        )
    else:
        limit_message = (
            "Fallback IDA* found no solution within the current depth bound of {0} "
            "moves after exploring {1} states and reaching depth {2}.".format(
                search_stats["searched_bound"],
                search_stats["expanded_nodes"],
                search_stats["deepest_depth"],
            )
        )

    cmds.warning(limit_message + " History fallback is disabled for testing.")
    
# Start a new scramble or request that the current scramble stop soon.
def scramble_cube(*_unused):
    global SCRAMBLE_ACTIVE
    global SCRAMBLE_PENDING_MOVES
    global SCRAMBLE_STOP_REQUESTED

    if PLAYBACK_ACTIVE:
        cmds.warning("Pause playback before starting a scramble")
        return

    if ALGORITHM_RUN_ACTIVE:
        cmds.warning("Stop the current algorithm before starting a scramble")
        return

    if SCRAMBLE_ACTIVE:
        # Clicking the button again does not interrupt the current turn. It only
        # requests that the scheduler stop before starting the next move.
        SCRAMBLE_STOP_REQUESTED = True
        update_scramble_button_label()
        return

    SCRAMBLE_ACTIVE = True
    SCRAMBLE_STOP_REQUESTED = False
    SCRAMBLE_PENDING_MOVES = generate_scramble().split()
    if get_animation_enabled():
        suspend_cached_playback_for_scramble()
    set_algorithm_field_text(format_move_sequence(SCRAMBLE_PENDING_MOVES))
    update_scramble_button_label()
    update_playback_ui()
    schedule_next_scramble_move()

# Queue the next scramble step so Maya can stay responsive.
def schedule_next_scramble_move():
    # Use a deferred/timer callback so Maya can process UI events between moves.
    if QtCore is not None:
        QtCore.QTimer.singleShot(0, process_next_scramble_move)
        return

    maya_utils.executeDeferred(process_next_scramble_move)

# Clear all scramble state and restore the button label.
def finish_scramble():
    global SCRAMBLE_ACTIVE
    global SCRAMBLE_PENDING_MOVES
    global SCRAMBLE_STOP_REQUESTED

    SCRAMBLE_ACTIVE = False
    SCRAMBLE_STOP_REQUESTED = False
    SCRAMBLE_PENDING_MOVES = []
    restore_cached_playback_after_scramble()
    update_scramble_button_label()
    update_playback_ui()

# Execute one pending scramble move, then reschedule if needed.
def process_next_scramble_move():
    global SCRAMBLE_PENDING_MOVES

    if not SCRAMBLE_ACTIVE:
        return

    if SCRAMBLE_STOP_REQUESTED or not SCRAMBLE_PENDING_MOVES:
        finish_scramble()
        return

    # Execute exactly one move, yield back to Maya, then schedule the next move.
    # This is what allows the scramble button to be clicked again mid-scramble.
    move_name = SCRAMBLE_PENDING_MOVES.pop(0)
    apply_move(move_name)

    if SCRAMBLE_STOP_REQUESTED or not SCRAMBLE_PENDING_MOVES:
        finish_scramble()
        return

    schedule_next_scramble_move()

# -------------------------
# Sequence Execution
# -------------------------
# Run a space-separated sequence of moves with or without animation.
def run_sequence(sequence, track_history=None):
    global current_time

    ensure_initial_state()
    animate = get_animation_enabled()
    try:
        move_names = parse_move_sequence(sequence, valid_moves=MOVES)
    except ValueError as error:
        cmds.warning(str(error))
        return

    if not move_names:
        return

    normalized_sequence = format_move_sequence(move_names)
    set_algorithm_field_text(normalized_sequence)

    if animate:
        # Animated sequences queue moves one after another on the timeline.
        sequence_frame = get_current_frame()
        for move_name in move_names:
            if SCRAMBLE_ACTIVE and SCRAMBLE_STOP_REQUESTED:
                break

            apply_move(
                move_name,
                animate=True,
                start_frame=sequence_frame,
                track_history=track_history,
            )
            sequence_frame += get_move_duration() + 2
        current_time = sequence_frame
        return

    for move_name in move_names:
        if SCRAMBLE_ACTIVE and SCRAMBLE_STOP_REQUESTED:
            break
        apply_move(move_name, animate=False, track_history=track_history)

# -------------------------
# Main Window UI
# -------------------------
# Build the main Maya window and wire up all controls.
def create_ui():
    global UI_MOVE_BUTTONS
    ensure_initial_state()
    cmds.currentTime(1)

    cmds.window("rubikUI", exists=True) and cmds.deleteUI("rubikUI")

    window = cmds.window("rubikUI", title="Rubik's Cube Controller")
    cmds.columnLayout(adjustableColumn=True)

    cmds.button(
        BUILD_CUBE_BUTTON,
        label="Build/Rebuild Cube",
        command=lambda *_: create_rubiks_cube(),
        annotation="Create the cube if it is missing, or rebuild it from scratch.",
    )

    cmds.separator(h=10)
    tabs = cmds.tabLayout(innerMarginWidth=10, innerMarginHeight=10)

    solve_tab = cmds.columnLayout(adjustableColumn=True)

    cmds.columnLayout(SOLVE_SETTINGS_SECTION, adjustableColumn=True)
    cmds.checkBox(
        ANIMATE_CHECKBOX,
        label="Animate / Add Keyframes",
        value=True,
        annotation="When enabled, moves create timeline keyframes instead of snapping instantly.",
    )
    cmds.intSliderGrp(
        MOVE_SPEED_SLIDER,
        label="Move Duration",
        field=True,
        min=2,
        max=30,
        fieldMinValue=1,
        fieldMaxValue=60,
        value=MOVE_DURATION,
        step=1,
        changeCommand=lambda *_: update_playback_ui(),
        dragCommand=lambda *_: update_playback_ui(),
        annotation="Adjust how many frames each animated move takes.",
        **get_slider_group_layout_kwargs()
    )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(MOVE_BUTTONS_SECTION, adjustableColumn=True)
    cmds.text(label="Cube Operations")
    moves_pane = cmds.paneLayout(configuration="vertical2", separatorThickness=6)
    cmds.columnLayout(adjustableColumn=True, parent=moves_pane)
    UI_MOVE_BUTTONS = {}
    for world_face in ("U", "D", "R", "L", "F", "B"):
        UI_MOVE_BUTTONS[(world_face, 1)] = cmds.button(
            label=world_face,
            annotation=get_move_button_tooltip(world_face, 1),
        )
    cmds.columnLayout(adjustableColumn=True, parent=moves_pane)
    for world_face in ("U", "D", "R", "L", "F", "B"):
        UI_MOVE_BUTTONS[(world_face, -1)] = cmds.button(
            label=world_face + "'",
            annotation=get_move_button_tooltip(world_face, -1),
        )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(ORIENTATION_SECTION, adjustableColumn=True)
    cmds.text(label="Cube Orientation")
    orientation_pane = cmds.paneLayout(configuration="vertical2", separatorThickness=6)
    cmds.columnLayout(adjustableColumn=True, parent=orientation_pane)
    for rotation_name in ("X", "Y", "Z"):
        cmds.button(
            label=rotation_name,
            command=lambda _unused, rotation_name=rotation_name: rotate_cube(rotation_name),
            annotation=ROTATION_BUTTON_TOOLTIPS[rotation_name],
        )
    cmds.columnLayout(adjustableColumn=True, parent=orientation_pane)
    for rotation_name in ("X", "Y", "Z"):
        cmds.button(
            label=rotation_name + "'",
            command=lambda _unused, rotation_name=rotation_name + "'": rotate_cube(rotation_name),
            annotation=ROTATION_BUTTON_TOOLTIPS[rotation_name + "'"],
        )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(ACTION_SECTION, adjustableColumn=True)
    cmds.button(
        CONTROLS_BUTTON,
        label="Create Viewport Controls",
        command=toggle_viewport_controls,
        annotation="Create or delete clickable viewport arrows around the cube.",
    )
    cmds.button(
        SCRAMBLE_BUTTON,
        label="Scramble",
        command=scramble_cube,
        annotation="Generate and run a random scramble sequence.",
    )
    cmds.button(
        "solveButton",
        label="Solve Cube",
        command=solve_from_history,
        annotation="Run the solver from the current logical cube state.",
    )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(ALGORITHM_SECTION, adjustableColumn=True)
    cmds.text(label="Algorithm Input / Export")
    cmds.scrollField(
        ALGORITHM_FIELD,
        wordWrap=False,
        text="",
        height=70,
        annotation="Paste or edit a move sequence here, such as R U R' U'.",
    )
    cmds.button(
        RUN_ALGORITHM_BUTTON,
        label="Run Algorithm",
        command=run_algorithm_from_field,
        annotation="Run the move sequence currently written in the algorithm field.",
    )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(PLAYBACK_SECTION, adjustableColumn=True)
    cmds.text(label="Playback / History")
    playback_pane = cmds.paneLayout(configuration="vertical2", separatorThickness=6)
    cmds.columnLayout(adjustableColumn=True, parent=playback_pane)
    cmds.button(
        PLAYBACK_PLAY_BUTTON,
        label="Play Forward",
        command=toggle_playback,
        annotation="Play forward from the current history position.",
    )
    cmds.button(
        PLAYBACK_STEP_FORWARD_BUTTON,
        label="Step Forward",
        command=redo_move,
        annotation="Advance one move forward through the recorded history.",
    )
    cmds.button(
        PLAYBACK_REDO_BUTTON,
        label="Redo",
        command=redo_move,
        annotation="Redo the next move in history.",
    )
    cmds.columnLayout(adjustableColumn=True, parent=playback_pane)
    cmds.button(
        PLAYBACK_STEP_BACK_BUTTON,
        label="Step Back",
        command=undo_move,
        annotation="Step backward one move through the recorded history.",
    )
    cmds.button(
        PLAYBACK_UNDO_BUTTON,
        label="Undo",
        command=undo_move,
        annotation="Undo the most recently applied move.",
    )
    cmds.button(
        label="Load Current History",
        command=load_move_history_into_algorithm_field,
        annotation="Copy the currently applied move history into the algorithm field.",
    )
    cmds.setParent(solve_tab)

    cmds.intSliderGrp(
        PLAYBACK_SCRUB_SLIDER,
        label="History Scrub",
        field=True,
        min=0,
        max=1,
        fieldMinValue=0,
        fieldMaxValue=1,
        value=0,
        step=1,
        dragCommand=scrub_to_history_position,
        changeCommand=scrub_to_history_position,
        annotation="Drag to jump to any point in the recorded move history.",
        **get_slider_group_layout_kwargs()
    )
    cmds.text(
        PLAYBACK_STATUS_TEXT,
        label="History Position: 0/0",
        align="left",
        annotation="Shows the current playback cursor and total recorded moves.",
    )
    cmds.button(
        label="Load Move History",
        command=load_move_history_into_algorithm_field,
        annotation="Load the applied history into the algorithm field.",
    )
    cmds.button(
        label="Load Inverse History",
        command=load_inverse_history_into_algorithm_field,
        annotation="Load the inverse of the applied history into the algorithm field.",
    )
    cmds.setParent(solve_tab)

    cmds.separator(h=10)

    cmds.columnLayout(RESET_SECTION, adjustableColumn=True)
    cmds.button(
        label="Clear Keyframes + Reset Cube",
        command=clear_animation_and_reset,
        annotation="Remove cube animation keys and restore the saved reset pose.",
    )
    cmds.button(
        label="Save Current Pose As Reset State",
        command=save_current_pose_as_initial_state,
        annotation="Use the current pose as the new reset state and solve target.",
    )
    cmds.setParent(tabs)

    aesthetic_tab = cmds.columnLayout(adjustableColumn=True)

    cmds.columnLayout(AESTHETICS_POLISH_SECTION, adjustableColumn=True)
    
    cmds.button(
        label="Reset Aesthetics",
        command=reset_aesthetics_to_defaults,
        annotation="Restore all appearance settings to their defaults while keeping the current theme preset.",
    )
    
    cmds.separator(h=10)
    
    cmds.text(label="Cube Polish")
    
    cmds.floatSliderGrp(
        "gapSpacingSlider",
        label="Gap Spacing",
        field=True,
        min=0.0,
        max=0.22,
        value=GAP_SPACING,
        step=0.01,
        precision=3,
        dragCommand=lambda *_: on_gap_spacing_changed(True),
        changeCommand=lambda *_: on_gap_spacing_changed(False),
        annotation="Increase the spacing between cubies without changing slice logic.",
        **get_slider_group_layout_kwargs()
    )
    cmds.floatSliderGrp(
        "stickerScaleSlider",
        label="Sticker Size",
        field=True,
        min=0.55,
        max=0.96,
        value=STICKER_SCALE,
        step=0.01,
        precision=3,
        dragCommand=lambda *_: on_sticker_scale_changed(True),
        changeCommand=lambda *_: on_sticker_scale_changed(False),
        annotation="Shrink or expand the sticker footprint on each visible face.",
        **get_slider_group_layout_kwargs()
    )
    cmds.floatSliderGrp(
        "stickerThicknessSlider",
        label="Sticker Depth",
        field=True,
        min=0.005,
        max=0.05,
        value=STICKER_THICKNESS,
        step=0.005,
        precision=3,
        dragCommand=lambda *_: on_sticker_thickness_changed(True),
        changeCommand=lambda *_: on_sticker_thickness_changed(False),
        annotation="Adjust how thick the sticker geometry sits above each cubie.",
        **get_slider_group_layout_kwargs()
    )
    cmds.floatSliderGrp(
        "stickerRoundnessSlider",
        label="Sticker Round",
        field=True,
        min=0.0,
        max=0.5,
        value=STICKER_ROUNDNESS,
        step=0.002,
        precision=3,
        dragCommand=lambda *_: on_sticker_roundness_changed(True),
        changeCommand=lambda *_: on_sticker_roundness_changed(False),
        annotation="Round off the sticker edges for a softer finished look.",
        **get_slider_group_layout_kwargs()
    )
    cmds.separator(h=10)
    cmds.setParent(aesthetic_tab)

    cmds.columnLayout(AESTHETICS_CONTROLS_SECTION, adjustableColumn=True)
    cmds.text(label="Viewport Controls")
    cmds.floatSliderGrp(
        "controlSizeSlider",
        label="Control Size",
        field=True,
        min=0.7,
        max=1.7,
        value=CONTROL_SIZE,
        step=0.05,
        precision=2,
        dragCommand=lambda *_: on_control_size_changed(True),
        changeCommand=lambda *_: on_control_size_changed(False),
        annotation="Resize the viewport arrows and push them outward a bit as they grow.",
        **get_slider_group_layout_kwargs()
    )
    cmds.floatSliderGrp(
        "controlOpacitySlider",
        label="Control Alpha",
        field=True,
        min=0.1,
        max=1.0,
        value=CONTROL_OPACITY,
        step=0.05,
        precision=2,
        dragCommand=lambda *_: on_control_opacity_changed(True),
        changeCommand=lambda *_: on_control_opacity_changed(False),
        annotation="Adjust the fill opacity of the viewport controls.",
        **get_slider_group_layout_kwargs()
    )
    cmds.separator(h=10)
    cmds.setParent(aesthetic_tab)

    cmds.columnLayout(AESTHETICS_ENVIRONMENT_SECTION, adjustableColumn=True)
    cmds.text(label="Environment")
    cmds.checkBox(
        "viewportBackgroundToggle",
        label="Theme Background",
        value=SHOW_VIEWPORT_BACKGROUND,
        changeCommand=on_viewport_background_toggled,
        annotation="Toggle a themed Maya viewport gradient background.",
    )
    cmds.checkBox(
        "floorGridToggle",
        label="Show Floor Grid",
        value=SHOW_FLOOR_GRID,
        changeCommand=on_floor_grid_toggled,
        annotation="Show or hide the Maya floor grid for cleaner presentation shots.",
    )
    cmds.checkBox(
        "presentationModeToggle",
        label="Presentation Mode",
        value=PRESENTATION_MODE,
        changeCommand=on_presentation_mode_toggled,
        annotation="Hide construction and history sections so the tool reads more like a player.",
    )
    cmds.separator(h=10)
    cmds.setParent(aesthetic_tab)

    cmds.columnLayout(AESTHETICS_THEME_SECTION, adjustableColumn=True)
    cmds.text(label="Theme Preset")
    cmds.optionMenu(
        "themeDropdown",
        label="Preset",
        changeCommand=on_theme_changed,
        annotation="Swap face colors and optional background styling together.",
    )
    for theme_name in THEME_PRESETS:
        cmds.menuItem(label=theme_name)
    cmds.separator(h=10)
    cmds.setParent(aesthetic_tab)

    cmds.columnLayout(AESTHETICS_BEVEL_SECTION, adjustableColumn=True)
    cmds.text(label="Bevel Settings")
    cmds.checkBox(
        "bevelToggle",
        label="Enable Bevel",
        value=BEVEL_ENABLED,
        changeCommand=lambda val: (set_bevel_enabled(val), create_rubiks_cube(preserve_scene_state=True)),
        annotation="Toggle beveled cube edges for a softer look.",
    )
    cmds.floatSliderGrp(
        "bevelFraction",
        label="Bevel Amount",
        field=True,
        min=0.01,
        max=0.2,
        value=BEVEL_FRACTION,
        step=0.01,
        precision=3,
        dragCommand=lambda *_: on_bevel_fraction_changed(),
        changeCommand=lambda *_: on_bevel_fraction_changed(),
        annotation="Control how wide the bevel is on each cubie edge.",
        **get_slider_group_layout_kwargs()
    )
    cmds.intSliderGrp(
        "bevelSegments",
        label="Bevel Segments",
        field=True,
        min=1,
        max=20,
        value=BEVEL_SEGMENTS,
        dragCommand=lambda *_: on_bevel_segments_changed(),
        changeCommand=lambda *_: on_bevel_segments_changed(),
        annotation="Set how many edge segments are used in the bevel.",
        **get_slider_group_layout_kwargs()
    )
    cmds.optionMenu(
        "miteringDropdown",
        label="Mitering",
        changeCommand=lambda val: (set_bevel_mitering(int(val)), create_rubiks_cube(preserve_scene_state=True)),
        annotation="Choose how beveled corners are connected.",
    )
    cmds.menuItem(label="0")
    cmds.menuItem(label="1")
    cmds.menuItem(label="2")
    cmds.checkBox(
        "chamferToggle",
        label="Chamfer",
        value=BEVEL_CHAMFER,
        changeCommand=lambda val: (set_bevel_chamfer(val), create_rubiks_cube(preserve_scene_state=True)),
        annotation="Switch between chamfered and non-chamfered bevel behavior.",
    )
    cmds.separator(h=10)
    cmds.setParent('..')

    cmds.tabLayout(
        tabs,
        edit=True,
        tabLabel=[
            (solve_tab, "Solving"),
            (aesthetic_tab, "Aesthetics")
        ]
    )

    update_controls_button_label()

    cmds.showWindow(window)
    cmds.optionMenu("themeDropdown", e=True, value=THEME_NAME)
    cmds.optionMenu("miteringDropdown", e=True, value=str(BEVEL_MITERING))
    update_scramble_button_label()
    update_run_algorithm_button_label()
    update_ui_move_buttons()
    update_playback_ui()
    apply_viewport_background()
    apply_floor_grid_visibility()
    update_presentation_mode_ui()

# Update the scramble button text to match the current scramble state.
def update_scramble_button_label():
    if not cmds.control(SCRAMBLE_BUTTON, exists=True):
        return

    label = "Scramble"
    if SCRAMBLE_ACTIVE:
        label = "Stopping..." if SCRAMBLE_STOP_REQUESTED else "Stop Scramble"

    cmds.button(SCRAMBLE_BUTTON, e=True, label=label)


def update_run_algorithm_button_label():
    if not cmds.control(RUN_ALGORITHM_BUTTON, exists=True):
        return

    label = "Run Algorithm"
    if ALGORITHM_RUN_ACTIVE:
        label = "Stopping..." if ALGORITHM_RUN_STOP_REQUESTED else "Stop Algorithm"

    cmds.button(RUN_ALGORITHM_BUTTON, e=True, label=label)

# Refresh the labels and callbacks of the move buttons for current orientation.
def update_ui_move_buttons():
    if not UI_MOVE_BUTTONS:
        return

    moves_enabled = not is_busy_with_sequence()

    for world_face in ("U", "D", "R", "L", "F", "B"):
        positive_move_name = get_mapped_move_for_world_button(world_face, 1)
        negative_move_name = get_mapped_move_for_world_button(world_face, -1)
        positive_button = UI_MOVE_BUTTONS.get((world_face, 1))
        negative_button = UI_MOVE_BUTTONS.get((world_face, -1))

        if positive_button and cmds.control(positive_button, exists=True):
            cmds.button(
                positive_button,
                e=True,
                enable=moves_enabled,
                label=world_face,
                annotation=get_move_button_tooltip(world_face, 1),
                command=lambda _unused, move_name=positive_move_name: move(move_name),
            )

        if negative_button and cmds.control(negative_button, exists=True):
            cmds.button(
                negative_button,
                e=True,
                enable=moves_enabled,
                label=world_face + "'",
                annotation=get_move_button_tooltip(world_face, -1),
                command=lambda _unused, move_name=negative_move_name: move(move_name),
            )
    
# -------------------------
# Viewport Controls
# -------------------------
# Build one viewport arrow control mesh/curve set for a cube face.
def create_face_control(name, position, normal, color):
    # Each viewport control is a flat arrow built from curve/mesh geometry and
    # then oriented onto one face of the cube.
    outer_radius = 1.30 * CONTROL_SIZE
    body_thickness = 0.42 * CONTROL_SIZE
    inner_radius = outer_radius - body_thickness
    start_angle = 225.0
    end_angle = -25.0
    arc_steps = 24

    # Compute a point on a flat circle used to shape the arrow outline.
    def circle_point(radius, angle_deg):
        angle = math.radians(angle_deg)
        return (
            radius * math.cos(angle),
            0,
            radius * math.sin(angle),
        )

    outer_points = []
    inner_points = []

    for i in range(arc_steps + 1):
        blend = float(i) / float(arc_steps)
        angle_deg = start_angle + (end_angle - start_angle) * blend
        outer_points.append(circle_point(outer_radius, angle_deg))
        inner_points.append(circle_point(inner_radius, angle_deg))

    end_outer = outer_points[-1]
    prev_outer = outer_points[-2]
    end_inner = inner_points[-1]
    prev_inner = inner_points[-2]

    mid_end = (
        (end_outer[0] + end_inner[0]) * 0.5,
        0,
        (end_outer[2] + end_inner[2]) * 0.5,
    )

    tangent = (
        (end_outer[0] - prev_outer[0]) + (end_inner[0] - prev_inner[0]),
        0,
        (end_outer[2] - prev_outer[2]) + (end_inner[2] - prev_inner[2]),
    )
    tangent_length = math.sqrt(tangent[0] * tangent[0] + tangent[2] * tangent[2]) or 1.0
    tangent = (
        tangent[0] / tangent_length,
        0,
        tangent[2] / tangent_length,
    )
    normal_2d = (-tangent[2], 0, tangent[0])

    tail_outer = outer_points[0]
    tail_inner = inner_points[0]
    tail_mid = (
        (tail_outer[0] + tail_inner[0]) * 0.5,
        0,
        (tail_outer[2] + tail_inner[2]) * 0.5,
    )
    tail_tangent = (
        outer_points[1][0] - tail_outer[0] + inner_points[1][0] - tail_inner[0],
        0,
        outer_points[1][2] - tail_outer[2] + inner_points[1][2] - tail_inner[2],
    )
    tail_tangent_length = math.sqrt(tail_tangent[0] * tail_tangent[0] + tail_tangent[2] * tail_tangent[2]) or 1.0
    tail_tangent = (
        tail_tangent[0] / tail_tangent_length,
        0,
        tail_tangent[2] / tail_tangent_length,
    )
    tail_normal = (-tail_tangent[2], 0, tail_tangent[0])
    tail_offset = 0.16 * CONTROL_SIZE
    tail_half_width = body_thickness * 0.5
    tail_cap = [
        (
            tail_mid[0] - tail_tangent[0] * tail_offset + tail_normal[0] * tail_half_width,
            0,
            tail_mid[2] - tail_tangent[2] * tail_offset + tail_normal[2] * tail_half_width,
        ),
        (
            tail_mid[0] - tail_tangent[0] * tail_offset - tail_normal[0] * tail_half_width,
            0,
            tail_mid[2] - tail_tangent[2] * tail_offset - tail_normal[2] * tail_half_width,
        ),
    ]

    head_length = 0.52 * CONTROL_SIZE
    head_width = 0.34 * CONTROL_SIZE
    arrow_tip = (
        mid_end[0] + tangent[0] * head_length,
        0,
        mid_end[2] + tangent[2] * head_length,
    )
    arrow_top = (
        mid_end[0] - tangent[0] * 0.06 + normal_2d[0] * head_width,
        0,
        mid_end[2] - tangent[2] * 0.06 + normal_2d[2] * head_width,
    )
    arrow_bottom = (
        mid_end[0] - tangent[0] * 0.06 - normal_2d[0] * head_width,
        0,
        mid_end[2] - tangent[2] * 0.06 - normal_2d[2] * head_width,
    )

    outline_points = [tail_cap[0]]
    outline_points.extend(outer_points)
    outline_points.extend([arrow_top, arrow_tip, arrow_bottom])
    outline_points.extend(reversed(inner_points))
    outline_points.append(tail_cap[1])
    outline_points.append(tail_cap[0])

    flip_axis = "scaleZ"

    # Mirror the authored outline so the reverse-direction arrow can be shown.
    def mirrored_points(points):
        mirrored = []
        for x, y, z in points:
            if flip_axis == "scaleZ":
                mirrored.append((x, y, -z))
            else:
                mirrored.append((-x, y, z))
        return mirrored

    # Rotate locally authored control geometry onto the requested cube face.
    def orient_control_geometry(*nodes):
        # The arrow is authored in one local plane, then rotated into the proper
        # world-space face orientation before being frozen.
        if normal == (1,0,0):
            cmds.rotate(0, 0, 90, *nodes)
        elif normal == (0,0,1):
            cmds.rotate(90, 0, 0, *nodes)

        if name == "ctrl_L" or name == "ctrl_F" or name == "ctrl_U":
            cmds.rotate(180, 0, 0, *nodes, r=True, os=True)

        if name == "ctrl_F" or name == "ctrl_U" or name == "ctrl_D":
            cmds.rotate(0, -90, 0, *nodes, r=True, os=True)
        elif name == "ctrl_B":
            cmds.rotate(0, 90, 0, *nodes, r=True, os=True)

        for node in nodes:
            cmds.xform(node, ws=True, t=position)
            cmds.makeIdentity(node, apply=True, t=1, r=1, s=1)

    # Mark shapes so forward/reverse variants can be toggled later.
    def tag_shapes(shapes, is_reverse):
        for shape in shapes:
            if not cmds.attributeQuery("isReverseShape", node=shape, exists=True):
                cmds.addAttr(shape, longName="isReverseShape", attributeType="bool")
            cmds.setAttr(shape + ".isReverseShape", 1 if is_reverse else 0)

    ctrl = cmds.curve(name=name, d=1, p=outline_points)
    fill_mesh = cmds.polyCreateFacet(
        p=outline_points[:-1],
        name=name + "_fill",
        ch=False,
    )[0]
    reverse_curve = cmds.curve(name=name + "_reverse", d=1, p=mirrored_points(outline_points))
    reverse_fill_mesh = cmds.polyCreateFacet(
        p=mirrored_points(outline_points[:-1]),
        name=name + "_fill_reverse",
        ch=False,
    )[0]

    outward_vector = [0.0, 0.0, 0.0]
    for index, value in enumerate(position):
        if abs(value) > 1e-6:
            outward_vector[index] = 1.0 if value > 0 else -1.0
            break

    orient_control_geometry(ctrl, fill_mesh, reverse_curve, reverse_fill_mesh)

    tag_shapes(cmds.listRelatives(ctrl, shapes=True, fullPath=True) or [], False)

    fill_shapes = cmds.listRelatives(fill_mesh, shapes=True, fullPath=True) or []
    for fill_shape in fill_shapes:
        parented_shapes = cmds.parent(fill_shape, ctrl, r=True, s=True) or []
        tag_shapes(parented_shapes, False)

    reverse_shapes = cmds.listRelatives(reverse_curve, shapes=True, fullPath=True) or []
    for reverse_shape in reverse_shapes:
        parented_shapes = cmds.parent(reverse_shape, ctrl, r=True, s=True) or []
        tag_shapes(parented_shapes, True)

    reverse_fill_shapes = cmds.listRelatives(reverse_fill_mesh, shapes=True, fullPath=True) or []
    for reverse_fill_shape in reverse_fill_shapes:
        parented_shapes = cmds.parent(reverse_fill_shape, ctrl, r=True, s=True) or []
        tag_shapes(parented_shapes, True)

    fill_shapes = [
        shape for shape in (cmds.listRelatives(ctrl, shapes=True, fullPath=True) or [])
        if cmds.nodeType(shape) == "mesh"
    ]

    fill_shader = create_control_fill_material(name, color)[1]
    for fill_shape in fill_shapes:
        cmds.polyNormal(fill_shape, normalMode=2, userNormalMode=0, ch=False)
        fill_parent = cmds.listRelatives(fill_shape, parent=True, fullPath=True) or []
        fill_target = fill_parent[0] if fill_parent else fill_shape
        normal_info = cmds.polyInfo(fill_target + ".f[0]", fn=True) or []
        if normal_info:
            values = normal_info[0].split()
            face_normal = [float(values[2]), float(values[3]), float(values[4])]
            dot = sum(face_normal[i] * outward_vector[i] for i in range(3))
            if dot < 0:
                cmds.polyNormal(fill_shape, normalMode=0, userNormalMode=0, ch=False)
        cmds.setAttr(fill_shape + ".doubleSided", 1)
        cmds.setAttr(fill_shape + ".opposite", 0)
        cmds.sets(fill_shape, e=True, forceElement=fill_shader)

    cmds.delete(fill_mesh)
    cmds.delete(reverse_curve)
    cmds.delete(reverse_fill_mesh)

    for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        if not cmds.attributeQuery("isReverseShape", node=shape, exists=True):
            cmds.addAttr(shape, longName="isReverseShape", attributeType="bool")
            cmds.setAttr(shape + ".isReverseShape", 0)
        if cmds.nodeType(shape) == "nurbsCurve":
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideRGBColors", 1)
            cmds.setAttr(shape + ".overrideColorRGB", *color)
            cmds.setAttr(shape + ".lineWidth", max(1.0, 2.0 * CONTROL_SIZE))

    cmds.addAttr(ctrl, longName="direction", attributeType="long", defaultValue=1)
    cmds.setAttr(ctrl + ".direction", e=True, keyable=False)
    cmds.addAttr(ctrl, longName="faceName", dataType="string")
    cmds.setAttr(ctrl + ".faceName", name.replace("ctrl_", ""), type="string")
    set_arrow_direction(ctrl, 1)

    return ctrl
    
# Create all six viewport controls around the cube.
def create_viewport_controls():
    ctrls = []
    control_distance = 2.0 + ((CONTROL_SIZE - 1.0) * 0.45)
    control_specs = [
        ("ctrl_U", (0, control_distance, 0), (0, 1, 0)),
        ("ctrl_D", (0, -control_distance, 0), (0, 1, 0)),
        ("ctrl_R", (control_distance, 0, 0), (1, 0, 0)),
        ("ctrl_L", (-control_distance, 0, 0), (1, 0, 0)),
        ("ctrl_F", (0, 0, control_distance), (0, 0, 1)),
        ("ctrl_B", (0, 0, -control_distance), (0, 0, 1)),
    ]

    for ctrl_name, position, normal in control_specs:
        # Colors follow the logical cube face currently pointing outward at that
        # world-space position.
        world_vector = [0, 0, 0]
        for index, value in enumerate(position):
            if abs(value) > 1e-6:
                world_vector[index] = 1 if value > 0 else -1
                break

        logical_face = get_logical_face_from_world_vector(world_vector)
        color = FACE_COLORS.get(logical_face, (1, 1, 1))
        ctrls.append(create_face_control(ctrl_name, position, normal, color))

    cmds.group(ctrls, name="rubik_controls_grp")

# Set every viewport control to show either forward or reverse arrows.
def set_all_viewport_control_directions(direction):
    if not cmds.objExists("rubik_controls_grp"):
        return

    for ctrl in cmds.listRelatives("rubik_controls_grp", children=True, type="transform") or []:
        if ctrl.startswith("ctrl_"):
            set_arrow_direction(ctrl, direction)

# Reset viewport controls to their default visible direction.
def reset_viewport_control_directions():
    global CONTROL_SHIFT_DIRECTION

    if not cmds.objExists("rubik_controls_grp"):
        return

    update_viewport_control_names_by_position()
    set_all_viewport_control_directions(1)
    CONTROL_SHIFT_DIRECTION = 1
    
# Toggle which shapes of a control are visible based on move direction.
def set_arrow_direction(ctrl, direction):
    if not cmds.objExists(ctrl):
        return

    # Each control contains both forward and reverse shapes. Toggling direction
    # simply swaps which set is visible.
    cmds.setAttr(ctrl + ".direction", direction)
    show_reverse = direction != 1

    for shape in cmds.listRelatives(ctrl, shapes=True, fullPath=True) or []:
        is_reverse = False
        if cmds.attributeQuery("isReverseShape", node=shape, exists=True):
            is_reverse = bool(cmds.getAttr(shape + ".isReverseShape"))

        cmds.setAttr(shape + ".visibility", 1 if is_reverse == show_reverse else 0)

# Infer the logical face represented by a control's current position.
def get_face_from_control_position(ctrl):
    if not cmds.objExists(ctrl):
        return None

    bbox = cmds.exactWorldBoundingBox(ctrl)
    x = (bbox[0] + bbox[3]) * 0.5
    y = (bbox[1] + bbox[4]) * 0.5
    z = (bbox[2] + bbox[5]) * 0.5
    components = [x, y, z]
    axis_index = max(range(3), key=lambda index: abs(components[index]))
    world_vector = [0, 0, 0]
    world_vector[axis_index] = 1 if components[axis_index] >= 0 else -1
    return get_logical_face_from_world_vector(world_vector)

# Infer the world-space face label represented by a control position.
def get_world_face_from_control_position(ctrl):
    if not cmds.objExists(ctrl):
        return None

    bbox = cmds.exactWorldBoundingBox(ctrl)
    x = (bbox[0] + bbox[3]) * 0.5
    y = (bbox[1] + bbox[4]) * 0.5
    z = (bbox[2] + bbox[5]) * 0.5
    components = [x, y, z]
    axis_index = max(range(3), key=lambda index: abs(components[index]))

    if axis_index == 0:
        return "R" if components[axis_index] >= 0 else "L"
    if axis_index == 1:
        return "U" if components[axis_index] >= 0 else "D"
    return "F" if components[axis_index] >= 0 else "B"

# Rewrite stored control face names after the cube orientation changes.
def update_viewport_control_names_by_position():
    controls = get_viewport_controls()
    if not controls:
        return

    for ctrl in controls:
        face = get_face_from_control_position(ctrl)
        if face is None:
            continue
        if not cmds.attributeQuery("faceName", node=ctrl, exists=True):
            cmds.addAttr(ctrl, longName="faceName", dataType="string")
        cmds.setAttr(ctrl + ".faceName", face, type="string")

# Update arrow preview direction based on the current Shift key state.
def update_shift_preview_state():
    global CONTROL_SHIFT_DIRECTION

    if not cmds.objExists("rubik_controls_grp"):
        CONTROL_SHIFT_DIRECTION = None
        return

    # Holding Shift previews the inverse move by flipping the visible arrow.
    mods = cmds.getModifiers()
    target_direction = -1 if (mods & 1) > 0 else 1
    if CONTROL_SHIFT_DIRECTION == target_direction:
        return

    set_all_viewport_control_directions(target_direction)
    CONTROL_SHIFT_DIRECTION = target_direction

# Force the viewport control preview arrows to a specific direction.
def set_shift_preview_direction(direction):
    global CONTROL_SHIFT_DIRECTION

    if not cmds.objExists("rubik_controls_grp"):
        CONTROL_SHIFT_DIRECTION = None
        return

    if CONTROL_SHIFT_DIRECTION == direction:
        return

    set_all_viewport_control_directions(direction)
    CONTROL_SHIFT_DIRECTION = direction

# Sync Shift preview state using Qt keyboard modifiers when available.
def sync_shift_preview_from_qt():
    if QtWidgets is None:
        update_shift_preview_state()
        return

    modifiers = QtWidgets.QApplication.keyboardModifiers()
    is_shift = bool(modifiers & QtCore.Qt.ShiftModifier)
    set_shift_preview_direction(-1 if is_shift else 1)

# Clear the Maya selection without adding undo noise.
def clear_selection_without_undo():
    # Clicking a viewport control should behave like pressing a button, not like
    # leaving clutter in the Maya selection/undo stack.
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.select(clear=True)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)

# Restore a selection list without creating a separate undo step.
def restore_selection_without_undo(selection):
    valid_selection = [node for node in selection if cmds.objExists(node)]

    cmds.undoInfo(stateWithoutFlush=False)
    try:
        if valid_selection:
            cmds.select(valid_selection, replace=True)
        else:
            cmds.select(clear=True)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)

# React to selection changes and trigger viewport controls when clicked.
def on_selection_changed(*_unused):
    global CONTROL_CLICK_ARMED
    global CONTROL_PROCESSING_SELECTION
    global CONTROL_SKIP_SELECTION_CHANGE

    if CONTROL_PROCESSING_SELECTION or CONTROL_SKIP_SELECTION_CHANGE:
        return

    # Only treat a selection change as a button press when it was armed by an
    # actual left-click in the viewport. This prevents context-menu actions
    # such as Hypershade/shader edits from accidentally triggering a move.
    if not CONTROL_CLICK_ARMED:
        return

    selection = cmds.ls(selection=True) or []
    if not selection:
        CONTROL_CLICK_ARMED = False
        return

    CONTROL_CLICK_ARMED = False
    CONTROL_PROCESSING_SELECTION = True
    try:
        trigger_selected_control()
    finally:
        CONTROL_PROCESSING_SELECTION = False

# Disable undo temporarily while simulating a control click.
def suspend_undo_for_control_click():
    global CONTROL_CLICK_UNDO_DISABLED

    if CONTROL_CLICK_UNDO_DISABLED:
        return

    cmds.undoInfo(stateWithoutFlush=False)
    CONTROL_CLICK_UNDO_DISABLED = True

# Re-enable undo after a simulated control click.
def resume_undo_for_control_click():
    global CONTROL_CLICK_UNDO_DISABLED

    if not CONTROL_CLICK_UNDO_DISABLED:
        return

    cmds.undoInfo(stateWithoutFlush=True)
    CONTROL_CLICK_UNDO_DISABLED = False

# Translate the current control selection into an actual cube move.
def trigger_selected_control():
    global CONTROL_SELECTION_BEFORE_CLICK
    global CONTROL_SKIP_SELECTION_CHANGE

    sel = cmds.ls(selection=True)
    if not sel:
        CONTROL_SELECTION_BEFORE_CLICK = []
        return

    obj = sel[0].split(".", 1)[0]

    if cmds.objectType(obj, isAType="shape"):
        parents = cmds.listRelatives(obj, parent=True, fullPath=False) or []
        if not parents:
            CONTROL_SELECTION_BEFORE_CLICK = []
            return
        obj = parents[0]

    short_name = obj.split("|")[-1]
    if short_name.endswith("Shape"):
        parents = cmds.listRelatives(short_name, parent=True, fullPath=False) or []
        if not parents:
            CONTROL_SELECTION_BEFORE_CLICK = []
            return
        obj = parents[0]
        short_name = obj.split("|")[-1]

    if not short_name.startswith("ctrl_"):
        CONTROL_SELECTION_BEFORE_CLICK = []
        return

    # Prefer deriving the move from the control's world position so viewport
    # controls continue to behave correctly after rotating the whole cube.
    world_face = get_world_face_from_control_position(short_name)
    face = world_face
    if not face:
        face = get_face_from_control_position(short_name)
    if not face and cmds.attributeQuery("faceName", node=short_name, exists=True):
        face = cmds.getAttr(short_name + ".faceName")
    if not face:
        fallback_face = short_name.replace("ctrl_", "", 1)
        if fallback_face in FACE_COLORS:
            face = fallback_face
    if face is None:
        CONTROL_SELECTION_BEFORE_CLICK = []
        return

    if QtWidgets is not None:
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        is_shift = bool(modifiers & QtCore.Qt.ShiftModifier)
    else:
        mods = cmds.getModifiers()
        is_shift = (mods & 1) > 0
    sync_shift_preview_from_qt()

    if world_face:
        direction = -1 if is_shift else 1
        move_name = get_mapped_move_for_world_button(world_face, direction)
    else:
        move_name = face + "'" if is_shift else face
    CONTROL_SKIP_SELECTION_CHANGE = True
    try:
        clear_selection_without_undo()
        move(move_name)
    finally:
        CONTROL_SELECTION_BEFORE_CLICK = []
        CONTROL_SKIP_SELECTION_CHANGE = False

# Clear control-related transient state when undo/redo happens.
def on_undo_or_redo(*_unused):
    global CONTROL_CLICK_ARMED
    global CONTROL_PROCESSING_SELECTION
    global CONTROL_SELECTION_BEFORE_CLICK
    global CONTROL_SKIP_SELECTION_CHANGE

    CONTROL_SKIP_SELECTION_CHANGE = True
    try:
        CONTROL_CLICK_ARMED = False
        CONTROL_PROCESSING_SELECTION = False
        CONTROL_SELECTION_BEFORE_CLICK = []
        clear_selection_without_undo()
    finally:
        CONTROL_SKIP_SELECTION_CHANGE = False

# Monitor Qt key/mouse events to keep viewport control previews in sync.
class ShiftPreviewEventFilter(QtCore.QObject if QtCore else object):
    # This event filter keeps the arrow preview responsive even before Maya's
    # normal selection callbacks have fired.
    def eventFilter(self, obj, event):
        global CONTROL_CLICK_ARMED
        global CONTROL_SELECTION_BEFORE_CLICK
        global CONTROL_SKIP_SELECTION_CHANGE

        if QtCore is None:
            return False

        event_type = event.type()
        if event_type == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Shift:
            set_shift_preview_direction(-1)
        elif event_type == QtCore.QEvent.KeyRelease and event.key() == QtCore.Qt.Key_Shift:
            set_shift_preview_direction(1)
        elif event_type == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton:
                CONTROL_CLICK_ARMED = True
                CONTROL_SKIP_SELECTION_CHANGE = False
                CONTROL_SELECTION_BEFORE_CLICK = cmds.ls(selection=True) or []
            else:
                CONTROL_CLICK_ARMED = False
                CONTROL_SELECTION_BEFORE_CLICK = []
        elif event_type == QtCore.QEvent.ContextMenu:
            CONTROL_CLICK_ARMED = False
            CONTROL_SELECTION_BEFORE_CLICK = []
        elif event_type in (
            QtCore.QEvent.FocusOut,
            QtCore.QEvent.WindowDeactivate,
            QtCore.QEvent.Leave,
        ):
            set_shift_preview_direction(1)
            CONTROL_CLICK_ARMED = False
            CONTROL_SELECTION_BEFORE_CLICK = []

        return False

# Install the Qt event filter used for Shift preview behavior.
def install_shift_preview_event_filter():
    global CONTROL_SHIFT_EVENT_FILTER

    if QtWidgets is None or wrapInstance is None or omui is None:
        return

    if CONTROL_SHIFT_EVENT_FILTER is not None:
        return

    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is None:
        return

    main_window = wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    CONTROL_SHIFT_EVENT_FILTER = ShiftPreviewEventFilter(main_window)
    main_window.installEventFilter(CONTROL_SHIFT_EVENT_FILTER)

# Remove the Qt event filter used for Shift preview behavior.
def remove_shift_preview_event_filter():
    global CONTROL_SHIFT_EVENT_FILTER

    if CONTROL_SHIFT_EVENT_FILTER is None or QtWidgets is None or wrapInstance is None or omui is None:
        CONTROL_SHIFT_EVENT_FILTER = None
        return

    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is not None:
        main_window = wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
        main_window.removeEventFilter(CONTROL_SHIFT_EVENT_FILTER)

    CONTROL_SHIFT_EVENT_FILTER = None

# Start a timer that continually syncs Shift preview state from Qt.
def install_shift_preview_timer():
    global CONTROL_SHIFT_TIMER

    if QtCore is None or QtWidgets is None:
        return

    if CONTROL_SHIFT_TIMER is not None:
        return

    CONTROL_SHIFT_TIMER = QtCore.QTimer()
    CONTROL_SHIFT_TIMER.setInterval(5)
    CONTROL_SHIFT_TIMER.timeout.connect(sync_shift_preview_from_qt)
    CONTROL_SHIFT_TIMER.start()

# Stop and destroy the timer used for Shift preview syncing.
def remove_shift_preview_timer():
    global CONTROL_SHIFT_TIMER

    if CONTROL_SHIFT_TIMER is not None:
        CONTROL_SHIFT_TIMER.stop()
        CONTROL_SHIFT_TIMER.deleteLater()
        CONTROL_SHIFT_TIMER = None

# Delete any scriptJobs created for viewport control behavior.
def clear_control_script_jobs():
    global CONTROL_SCRIPT_JOBS

    for job_id in CONTROL_SCRIPT_JOBS:
        if cmds.scriptJob(exists=job_id):
            cmds.scriptJob(kill=job_id, force=True)

    CONTROL_SCRIPT_JOBS = []

# Remove leftover selection scriptJobs from previous script runs.
def clear_stale_selection_script_jobs():
    for job in cmds.scriptJob(listJobs=True) or []:
        if "SelectionChanged" not in job:
            continue

        if "on_selection_changed" not in job:
            continue

        try:
            job_id = int(job.split(":", 1)[0])
        except (TypeError, ValueError):
            continue

        if cmds.scriptJob(exists=job_id):
            cmds.scriptJob(kill=job_id, force=True)
    
# Create the scriptJobs and Qt hooks that make viewport controls interactive.
def setup_control_click_behavior():
    global CONTROL_CLICK_ARMED
    global CONTROL_SKIP_SELECTION_CHANGE
    global CONTROL_SCRIPT_JOBS

    # Viewport controls rely on a mix of scriptJobs and Qt hooks:
    # - SelectionChanged turns a click into a cube move.
    # - Undo/Redo clears stale selection state.
    # - Qt key/mouse events keep the Shift-preview arrows in sync.
    CONTROL_CLICK_ARMED = False
    CONTROL_SKIP_SELECTION_CHANGE = False
    clear_stale_selection_script_jobs()
    clear_control_script_jobs()
    CONTROL_SCRIPT_JOBS.append(
        cmds.scriptJob(event=["SelectionChanged", on_selection_changed])
    )
    CONTROL_SCRIPT_JOBS.append(
        cmds.scriptJob(event=["Undo", on_undo_or_redo])
    )
    CONTROL_SCRIPT_JOBS.append(
        cmds.scriptJob(event=["Redo", on_undo_or_redo])
    )
    remove_shift_preview_event_filter()
    install_shift_preview_event_filter()
    install_shift_preview_timer()
    sync_shift_preview_from_qt()

# Rebuild the viewport controls and prepare them for interaction.
def setup_viewport_controls():
    delete_existing_controls()
    create_viewport_controls()
    setup_control_click_behavior()
    reset_viewport_control_directions()
    cmds.setToolTo("selectSuperContext")
    
# Remove viewport controls and tear down their callbacks.
def delete_existing_controls():
    clear_stale_selection_script_jobs()
    clear_control_script_jobs()
    remove_shift_preview_event_filter()
    remove_shift_preview_timer()
    if cmds.objExists("rubik_controls_grp"):
        cmds.delete("rubik_controls_grp")
        
# Toggle viewport controls on or off from the UI button.
def toggle_viewport_controls(*_unused):
    if cmds.objExists("rubik_controls_grp"):
        delete_existing_controls()
    else:
        setup_viewport_controls()

    update_controls_button_label()
    
# Refresh the viewport-controls button label to match current state.
def update_controls_button_label():
    if not cmds.control(CONTROLS_BUTTON, exists=True):
        return

    if cmds.objExists("rubik_controls_grp"):
        cmds.button(
            CONTROLS_BUTTON,
            e=True,
            label="Delete Viewport Controls",
            annotation="Delete the clickable viewport arrows around the cube.",
        )
    else:
        cmds.button(
            CONTROLS_BUTTON,
            e=True,
            label="Create Viewport Controls",
            annotation="Create clickable viewport arrows around the cube.",
        )

# -------------------------
# Script Startup
# -------------------------
# When the script is re-run in Maya, rebuild the tool cleanly instead of
# leaving duplicate controls, scriptJobs, or stale cube transforms behind.
if get_all_cubies():
        cmds.delete(get_all_cubies())
delete_existing_controls()
delete_existing_tool_materials()
clear_stale_selection_script_jobs()
apply_theme(THEME_NAME)
apply_viewport_background()
create_ui()
