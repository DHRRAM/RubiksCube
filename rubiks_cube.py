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

SCRIPT_DIRECTORY = None
if "__file__" in globals():
    SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
    if SCRIPT_DIRECTORY and SCRIPT_DIRECTORY not in sys.path:
        sys.path.insert(0, SCRIPT_DIRECTORY)

standalone_solver_core = None
STANDALONE_SOLVER_IMPORT_ERROR = None
STANDALONE_SOLVER_MODULE_MTIME = None
TOOL_DIRECTORY_OPTIONVAR = "RubiksCubeToolDirectory"

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

# MOVE_HISTORY is still useful as a fallback, but solving now prefers the
# logical cube-state search rather than relying on session history alone.
MOVE_HISTORY = []
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


# Return the standalone solver core module when it is importable from disk.
def get_standalone_solver_core():
    global standalone_solver_core
    global STANDALONE_SOLVER_IMPORT_ERROR
    global STANDALONE_SOLVER_MODULE_MTIME

    module_name = "rubiks_solver_core"
    module_path = None
    search_directories = []
    seen_directories = set()

    def add_search_directory(path):
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
        search_directories.append(normalized_path)

    # Maya does not always execute scripts like normal Python modules. Search a
    # few likely roots and remember successful hits in an optionVar so the
    # standalone solver can still be found after Script Editor execution.
    add_search_directory(SCRIPT_DIRECTORY)
    add_search_directory(getattr(sys.modules.get(__name__), "__file__", None))
    code_object = getattr(get_standalone_solver_core, "__code__", None)
    if code_object is not None:
        add_search_directory(code_object.co_filename)
    add_search_directory(os.getcwd())
    add_search_directory(os.environ.get("RUBIKS_CUBE_TOOL_DIR"))
    try:
        add_search_directory(cmds.internalVar(userScriptDir=True))
    except Exception:
        pass
    try:
        if cmds.optionVar(exists=TOOL_DIRECTORY_OPTIONVAR):
            add_search_directory(cmds.optionVar(q=TOOL_DIRECTORY_OPTIONVAR))
    except Exception:
        pass

    for path in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        add_search_directory(path)
    for path in sys.path:
        add_search_directory(path)

    for directory in search_directories:
        candidate_path = os.path.join(directory, module_name + ".py")
        if os.path.exists(candidate_path):
            module_path = candidate_path
            try:
                cmds.optionVar(sv=(TOOL_DIRECTORY_OPTIONVAR, directory))
            except Exception:
                pass
            break

    try:
        if module_path:
            module_mtime = os.path.getmtime(module_path)
            if (
                standalone_solver_core is None
                or STANDALONE_SOLVER_MODULE_MTIME is None
                or module_mtime != STANDALONE_SOLVER_MODULE_MTIME
                or os.path.abspath(getattr(standalone_solver_core, "__file__", "")) != module_path
            ):
                module_spec = importlib.util.spec_from_file_location(module_name, module_path)
                if module_spec is None or module_spec.loader is None:
                    raise ImportError(
                        "Could not build an import spec for {0}".format(module_path)
                    )

                # Load directly from the discovered file path rather than
                # relying on Maya's import state to mirror a normal package.
                standalone_solver_core = importlib.util.module_from_spec(module_spec)
                sys.modules[module_name] = standalone_solver_core
                module_spec.loader.exec_module(standalone_solver_core)
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

SHADER_TYPE = "aiStandardSurface" # Or "lambert"

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

# Store which shader type new materials should use.
def set_shader_type(val):
    global SHADER_TYPE
    SHADER_TYPE = val
    
# React to bevel width changes and rebuild the cube.
def on_bevel_fraction_changed():
    global BEVEL_FRACTION

    BEVEL_FRACTION = cmds.floatSliderGrp("bevelFraction", q=True, value=True)
    create_rubiks_cube()

# React to bevel segment changes and rebuild the cube.
def on_bevel_segments_changed():
    global BEVEL_SEGMENTS

    BEVEL_SEGMENTS = cmds.intSliderGrp("bevelSegments", q=True, value=True)
    create_rubiks_cube()

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
        shader = cmds.shadingNode(SHADER_TYPE, asShader=True, name=name)
        if SHADER_TYPE == "aiStandardSurface":
            cmds.setAttr(shader + ".baseColor", *color, type="double3")
        elif SHADER_TYPE == "lambert":
            cmds.setAttr(shader + ".color", *color, type="double3")
    else:
        shader = name

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
    return {
        "white": create_material("rubiks_white", (1, 1, 1)),
        "yellow": create_material("rubiks_yellow", (1, 1, 0)),
        "red": create_material("rubiks_red", (1, 0, 0)),
        "orange": create_material("rubiks_orange", (1, 0.5, 0)),
        "blue": create_material("rubiks_blue", (0, 0, 1)),
        "green": create_material("rubiks_green", (0, 1, 0)),
        "black": create_material("rubiks_black", (0.02, 0.02, 0.02)),
    }
    
# -------------------------
# Cubie Face Coloring
# -------------------------
# Color the visible faces of one cubie based on its grid position.
def assign_face_materials(cube, materials, pos):
    faces = cmds.polyEvaluate(cube, face=True)
    
    # Make everything black first
    for i in range(faces):
        cmds.sets(
        f"{cube}.f[{i}]",
        e=True,
        forceElement=materials["black"][1]
    )

    # Overwrite visible faces with appropriate shader
    for i in range(faces):
        face = f"{cube}.f[{i}]"

        # Get normal properly
        info = cmds.polyInfo(face, fn=True)[0].split()
        nx, ny, nz = float(info[2]), float(info[3]), float(info[4])

        # Use tolerance
        if ny > 0.99 and pos[1] == 1:
            cmds.sets(face, e=True, forceElement=materials["white"][1])

        elif ny < -0.99 and pos[1] == -1:
            cmds.sets(face, e=True, forceElement=materials["yellow"][1])

        elif nx > 0.99 and pos[0] == 1:
            cmds.sets(face, e=True, forceElement=materials["blue"][1])

        elif nx < -0.99 and pos[0] == -1:
            cmds.sets(face, e=True, forceElement=materials["green"][1])

        elif nz > 0.99 and pos[2] == 1:
            cmds.sets(face, e=True, forceElement=materials["red"][1])

        elif nz < -0.99 and pos[2] == -1:
            cmds.sets(face, e=True, forceElement=materials["orange"][1])

# -------------------------
# Cube Construction
# -------------------------
# Rebuild the full 3x3x3 Rubik's Cube mesh set.
def create_rubiks_cube():
    materials = setup_materials()
    
    # Delete old cubes
    existing = get_all_cubies()
    if existing:
        cmds.delete(existing)
    
    size = 1.0 # change to affect spacing between cubies
    
    for x in [-1, 0, 1]:
        for y in [-1, 0, 1]:
            for z in [-1, 0, 1]:
                cube = cmds.polyCube(w=size, h=size, d=size, ch=False)[0]
                
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

    reset_orientation()
    initialize_solver_state()
    MOVE_HISTORY.clear()
    capture_initial_state()
    update_ui_move_buttons()

    if cmds.objExists("rubik_controls_grp"):
        setup_viewport_controls()
    else:
        reset_viewport_control_directions()

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

        shapes = cmds.listRelatives(obj, shapes=True)
        if shapes and any(cmds.nodeType(shape) == "mesh" for shape in shapes):
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
    capture_initial_state()
    save_solver_goal_from_current_state()
    MOVE_HISTORY.clear()
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

    if move_name not in MOVES:
        print("Invalid move")
        return False

    if animate is None:
        animate = get_animation_enabled()

    if track_history is None:
        track_history = TRACK_MOVES

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
        MOVE_HISTORY.append(move_name)

    ensure_solver_state()
    CUBE_STATE = apply_move_to_cube_state(CUBE_STATE, move_name)
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


# Remove translation/rotation keys from a cubie set.
def clear_transform_keys(cubies, time_range=None):
    if not cubies:
        return

    kwargs = {"clear": True}
    if time_range is not None:
        kwargs["time"] = time_range

    for attr in ANIMATED_ATTRS:
        cmds.cutKey(cubies, at=attr, **kwargs)


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

    start = get_current_frame() if start_frame is None else start_frame
    end = start + MOVE_DURATION
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
        cmds.setAttr(shader + ".opacity", 0.5, 0.5, 0.5, type="double3")
    else:
        shader = shader_name
        cmds.setAttr(shader + ".base", 1.0)
        cmds.setAttr(shader + ".baseColor", *color, type="double3")
        cmds.setAttr(shader + ".specular", 0.0)
        cmds.setAttr(shader + ".opacity", 0.75, 0.75, 0.75, type="double3")

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

# Return the face letter for a move like R, R', or R2.
def get_move_face(move_name):
    return move_name[0]

# Return the inverse of a move name, including 180-degree turns.
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

# Expand solver-only moves like R2 into quarter turns Maya can execute.
def expand_solver_moves(move_names):
    expanded = []
    for move_name in move_names:
        expanded.extend(MOVE_EXPANSIONS.get(move_name, (move_name,)))
    return expanded

# Apply safe search pruning rules to avoid obviously redundant branches.
def should_prune_search_move(last_move, move_name):
    if not last_move:
        return False

    last_face = get_move_face(last_move)
    face = get_move_face(move_name)

    # Same-face repeats are redundant once the search includes 180-degree turns.
    if face == last_face:
        return True

    # Opposite-face turns on the same axis commute, so keep one canonical order.
    if FACE_AXES[face] == FACE_AXES[last_face]:
        return OPPOSITE_FACE_ORDER[face] < OPPOSITE_FACE_ORDER[last_face]

    return False

# Apply one search move to a single logical piece state.
def apply_move_to_piece_state(piece_state, move_name):
    axis, value, angle = SEARCH_MOVES[move_name]
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
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

# Build the canonical list of logical corner and edge pieces.
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

                pieces.append({
                    "home_position": position,
                    "faces": tuple(faces),
                    "piece_type": "corner" if magnitude == 3 else "edge",
                })

    pieces.sort(
        key=lambda piece: (
            piece["piece_type"],
            piece["home_position"][1],
            piece["home_position"][0],
            piece["home_position"][2],
        )
    )
    return tuple(pieces)

# Rotate a logical position or sticker direction by a 90-degree turn.
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

# Build the immutable logical solved piece states used to seed the catalogs.
def build_solved_piece_states():
    return tuple(
        (piece["home_position"],) + tuple(FACE_VECTORS[face] for face in piece["faces"])
        for piece in SOLVER_PIECES
    )

# Enumerate every reachable state code for each individual piece.
def build_piece_state_catalogs():
    state_infos = []
    state_to_code = []
    position_ids = []
    orientation_ids = []

    for solved_piece_state in SOLVED_PIECE_STATES:
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

# Precompute how every solver move transforms every encoded piece state.
def build_move_state_tables():
    move_tables = {}

    for move_name in SEARCH_MOVE_NAMES:
        per_piece_tables = []

        for piece_index, piece_states in enumerate(PIECE_STATE_INFOS):
            piece_lookup = PIECE_STATE_TO_CODE[piece_index]
            per_piece_tables.append(
                tuple(
                    piece_lookup[apply_move_to_piece_state(piece_state, move_name)]
                    for piece_state in piece_states
                )
            )

        move_tables[move_name] = tuple(per_piece_tables)

    return move_tables

# Build the immutable encoded solved state used as the search target.
def build_solved_cube_state():
    return tuple(
        PIECE_STATE_TO_CODE[piece_index][SOLVED_PIECE_STATES[piece_index]]
        for piece_index in range(len(SOLVED_PIECE_STATES))
    )

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
    for move_name in move_names:
        state = apply_move_to_cube_state(state, move_name)
    return state

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
    move_table = MOVE_STATE_TABLES[move_name]
    return tuple(
        move_table[piece_index][piece_state_code]
        for piece_index, piece_state_code in enumerate(state)
    )

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
    move_names = list(MOVES.keys())
    scramble = []
    previous_face = None

    for _unused in range(length):
        # Avoid immediately repeating the same face twice in a row so scrambles
        # feel more natural and don't waste moves like R R'.
        valid_moves = [move_name for move_name in move_names if move_name[0] != previous_face]
        move_name = random.choice(valid_moves)
        scramble.append(move_name)
        previous_face = move_name[0]

    return " ".join(scramble)


# Apply a single move from a UI button or viewport control.
def move(move_name):
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
    ensure_solver_state()
    CUBE_STATE = SOLVED_CUBE_STATE

    if controls_enabled:
        setup_viewport_controls()
    else:
        reset_viewport_control_directions()

    update_ui_move_buttons()

    current_time = 1

# Reverse a move sequence by reversing order and inverting each move.
def reverse_sequence(sequence):
    reverse = []
    for move in reversed(sequence.split()):
        reverse.append(INVERSE_MOVES.get(move, move))
    return " ".join(reverse)
    
# Solve the current logical cube state with the strongest available solver path.
def solve_from_history(*_unused):
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
                    run_sequence(solution, track_history=False)
                MOVE_HISTORY.clear()
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
            run_sequence(solution, track_history=False)
        MOVE_HISTORY.clear()
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

    if SCRAMBLE_ACTIVE:
        # Clicking the button again does not interrupt the current turn. It only
        # requests that the scheduler stop before starting the next move.
        SCRAMBLE_STOP_REQUESTED = True
        update_scramble_button_label()
        return

    SCRAMBLE_ACTIVE = True
    SCRAMBLE_STOP_REQUESTED = False
    SCRAMBLE_PENDING_MOVES = generate_scramble().split()
    update_scramble_button_label()
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
    update_scramble_button_label()

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

    if animate:
        # Animated sequences queue moves one after another on the timeline.
        sequence_frame = get_current_frame()
        for move_name in sequence.split():
            if SCRAMBLE_ACTIVE and SCRAMBLE_STOP_REQUESTED:
                break

            if move_name not in MOVES:
                print("Invalid move: {0}".format(move_name))
                continue

            apply_move(
                move_name,
                animate=True,
                start_frame=sequence_frame,
                track_history=track_history,
            )
            sequence_frame += MOVE_DURATION + 2
        current_time = sequence_frame
        return

    for move_name in sequence.split():
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
    # Treat re-running the script as a fresh tool launch and jump the Maya
    # timeline back to the default starting frame.
    cmds.currentTime(1)

    cmds.window("rubikUI", exists=True) and cmds.deleteUI("rubikUI")
    
    window = cmds.window("rubikUI", title="Rubik's Cube Controller")
    
    main_layout = cmds.columnLayout(adjustableColumn=True)
    
    cmds.button(
        label="Build/Rebuild Cube",
        command=lambda *_: create_rubiks_cube()
    )
    
    cmds.separator(h=8)
    
    # Create tabs below button
    tabs = cmds.tabLayout(innerMarginWidth=10, innerMarginHeight=10)
    
    # -------------------------
    # Tab 1: Solving
    # -------------------------
    solve_tab = cmds.columnLayout(adjustableColumn=True)
    
    cmds.checkBox(
        ANIMATE_CHECKBOX,
        label="Animate / Add Keyframes",
        value=True,
    )
    
    cmds.separator(h=8)
    
    cmds.text(label="Cube Operations")
    moves_pane = cmds.paneLayout(configuration="vertical2", separatorThickness=6)
    cmds.columnLayout(adjustableColumn=True, parent=moves_pane)
    UI_MOVE_BUTTONS = {}
    for world_face in ("U", "D", "R", "L", "F", "B"):
        UI_MOVE_BUTTONS[(world_face, 1)] = cmds.button(label=world_face)
    cmds.columnLayout(adjustableColumn=True, parent=moves_pane)
    for world_face in ("U", "D", "R", "L", "F", "B"):
        UI_MOVE_BUTTONS[(world_face, -1)] = cmds.button(label=world_face + "'")
    cmds.setParent(solve_tab)
    
    cmds.separator(h=10)
    
    cmds.text(label="Cube Orientation")
    orientation_pane = cmds.paneLayout(configuration="vertical2", separatorThickness=6)
    cmds.columnLayout(adjustableColumn=True, parent=orientation_pane)
    for rotation_name in ("X", "Y", "Z"):
        cmds.button(
            label=rotation_name,
            command=lambda _unused, rotation_name=rotation_name: rotate_cube(rotation_name),
        )
    cmds.columnLayout(adjustableColumn=True, parent=orientation_pane)
    for rotation_name in ("X", "Y", "Z"):
        cmds.button(
            label=rotation_name + "'",
            command=lambda _unused, rotation_name=rotation_name + "'": rotate_cube(rotation_name),
        )
    cmds.setParent(solve_tab)
    
    cmds.separator(h=10)
    
    cmds.button(
        "controlsButton",
        label="Create Viewport Controls",
        command=toggle_viewport_controls
    )
    
    cmds.button(
        SCRAMBLE_BUTTON,
        label="Scramble",
        command=scramble_cube,
    )
    
    cmds.button(
        "solveButton",
        label="Solve Cube",
        command=solve_from_history,
    )
    
    cmds.button(
        label="Clear Keyframes + Reset Cube",
        command=clear_animation_and_reset,
    )
    
    cmds.button(
        label="Save Current Pose As Reset State",
        command=save_current_pose_as_initial_state,
    )
    
    cmds.setParent('..')
    
    # -------------------------
    # Tab 2: Aesthetics
    # -------------------------
    aesthetic_tab = cmds.columnLayout(adjustableColumn=True)
    
    cmds.text(label="Bevel Settings")
    
    cmds.checkBox(
        "bevelToggle",
        label="Enable Bevel",
        value=BEVEL_ENABLED,
        changeCommand=lambda val: (set_bevel_enabled(val), create_rubiks_cube())
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
        changeCommand=lambda *_: on_bevel_fraction_changed()
    )
    
    cmds.intSliderGrp(
        "bevelSegments",
        label="Bevel Segments",
        field=True,
        min=1,
        max=5,
        value=BEVEL_SEGMENTS,
    
        dragCommand=lambda *_: on_bevel_segments_changed(),
        changeCommand=lambda *_: on_bevel_segments_changed()
    )
    
    cmds.optionMenu(
        "miteringDropdown",
        label="Mitering",
        changeCommand=lambda val: (set_bevel_mitering(int(val)), create_rubiks_cube())
    )
    
    cmds.menuItem(label="0")
    cmds.menuItem(label="1")
    cmds.menuItem(label="2") 
    
    cmds.checkBox(
        "chamferToggle",
        label="Chamfer",
        value=BEVEL_CHAMFER,
        changeCommand=lambda val: (set_bevel_chamfer(val), create_rubiks_cube())
    )       
    
    cmds.separator(h=10)
    
    cmds.text(label="Shader Type")
    
    cmds.optionMenu(
        "shaderDropdown",
        changeCommand=lambda val: (set_shader_type(val), create_rubiks_cube())
    )
    
    cmds.menuItem(label="aiStandardSurface")
    cmds.menuItem(label="lambert")
    
    cmds.separator(h=10)
    
    cmds.setParent('..')
    
    # -------------------------
    # Tab Labels
    # -------------------------
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
    update_ui_move_buttons()
    update_scramble_button_label()

# Update the scramble button text to match the current scramble state.
def update_scramble_button_label():
    if not cmds.control(SCRAMBLE_BUTTON, exists=True):
        return

    label = "Scramble"
    if SCRAMBLE_ACTIVE:
        label = "Stopping..." if SCRAMBLE_STOP_REQUESTED else "Stop Scramble"

    cmds.button(SCRAMBLE_BUTTON, e=True, label=label)

# Refresh the labels and callbacks of the move buttons for current orientation.
def update_ui_move_buttons():
    if not UI_MOVE_BUTTONS:
        return

    for world_face in ("U", "D", "R", "L", "F", "B"):
        positive_move_name = get_mapped_move_for_world_button(world_face, 1)
        negative_move_name = get_mapped_move_for_world_button(world_face, -1)
        positive_button = UI_MOVE_BUTTONS.get((world_face, 1))
        negative_button = UI_MOVE_BUTTONS.get((world_face, -1))

        if positive_button and cmds.control(positive_button, exists=True):
            cmds.button(
                positive_button,
                e=True,
                label=world_face,
                command=lambda _unused, move_name=positive_move_name: move(move_name),
            )

        if negative_button and cmds.control(negative_button, exists=True):
            cmds.button(
                negative_button,
                e=True,
                label=world_face + "'",
                command=lambda _unused, move_name=negative_move_name: move(move_name),
            )
    
# -------------------------
# Viewport Controls
# -------------------------
# Build one viewport arrow control mesh/curve set for a cube face.
def create_face_control(name, position, normal, color):
    # Each viewport control is a flat arrow built from curve/mesh geometry and
    # then oriented onto one face of the cube.
    outer_radius = 1.30
    body_thickness = 0.42
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
    tail_offset = 0.16
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

    head_length = 0.52
    head_width = 0.34
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
            cmds.setAttr(shape + ".lineWidth", 2)

    cmds.addAttr(ctrl, longName="direction", attributeType="long", defaultValue=1)
    cmds.setAttr(ctrl + ".direction", e=True, keyable=False)
    cmds.addAttr(ctrl, longName="faceName", dataType="string")
    cmds.setAttr(ctrl + ".faceName", name.replace("ctrl_", ""), type="string")
    set_arrow_direction(ctrl, 1)

    return ctrl
    
# Create all six viewport controls around the cube.
def create_viewport_controls():
    ctrls = []
    control_specs = [
        ("ctrl_U", (0, 2, 0), (0, 1, 0)),
        ("ctrl_D", (0, -2, 0), (0, 1, 0)),
        ("ctrl_R", (2, 0, 0), (1, 0, 0)),
        ("ctrl_L", (-2, 0, 0), (1, 0, 0)),
        ("ctrl_F", (0, 0, 2), (0, 0, 1)),
        ("ctrl_B", (0, 0, -2), (0, 0, 1)),
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
    global CONTROL_PROCESSING_SELECTION
    global CONTROL_SKIP_SELECTION_CHANGE

    if CONTROL_PROCESSING_SELECTION or CONTROL_SKIP_SELECTION_CHANGE:
        return

    selection = cmds.ls(selection=True) or []
    if not selection:
        return

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
            CONTROL_CLICK_ARMED = True
            CONTROL_SKIP_SELECTION_CHANGE = False
            CONTROL_SELECTION_BEFORE_CLICK = cmds.ls(selection=True) or []
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
    if not cmds.control("controlsButton", exists=True):
        return

    if cmds.objExists("rubik_controls_grp"):
        cmds.button("controlsButton", e=True, label="Delete Viewport Controls")
    else:
        cmds.button("controlsButton", e=True, label="Create Viewport Controls")

# -------------------------
# Script Startup
# -------------------------
# When the script is re-run in Maya, rebuild the tool cleanly instead of
# leaving duplicate controls, scriptJobs, or stale cube transforms behind.
if get_all_cubies():
        cmds.delete(get_all_cubies())
delete_existing_controls()
clear_stale_selection_script_jobs()
create_ui()