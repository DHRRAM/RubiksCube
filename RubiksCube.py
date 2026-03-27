import math
import random

import maya.cmds as cmds

try:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
    import maya.OpenMayaUI as omui
except ImportError:
    QtCore = None
    QtWidgets = None
    wrapInstance = None
    omui = None

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

# MOVE_HISTORY is used by the simple "solve" action, which just reverses every
# move the user or scrambler has applied.
MOVE_HISTORY = []
TRACK_MOVES = True

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
    if move_name not in MOVES:
        print("Invalid move")
        return False

    if animate is None:
        animate = get_animation_enabled()

    if track_history is None:
        track_history = TRACK_MOVES

    if track_history:
        MOVE_HISTORY.append(move_name)

    world_axis, world_value, world_angle = get_world_rotation_for_move(move_name)
    rotate_slice(
        world_axis,
        world_value,
        world_angle,
        animate=animate,
        start_frame=start_frame,
    )
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
    if QtWidgets is None:
        return

    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.processEvents()


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
        return

    rotate_cubies(
        cubies,
        axis,
        angle,
        animate=animate,
        start_frame=start_frame,
        clear_future=animate,
    )


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
        if "'" in move:
            reverse.append(move.replace("'", ""))
        else:
            reverse.append(move + "'")
    return " ".join(reverse)
    
# Solve the cube by playing back the inverse of recorded move history.
def solve_from_history(*_unused):
    if not MOVE_HISTORY:
        cmds.warning("Cube is already solved")
        return

    print("Solving cube...")

    solution = reverse_sequence(" ".join(MOVE_HISTORY))
    run_sequence(solution, track_history=False)

    # Clear history after solving
    MOVE_HISTORY.clear()
    
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

    cmds.evalDeferred("process_next_scramble_move()")

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
