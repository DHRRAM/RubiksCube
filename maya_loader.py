"""Maya bootstrap for importing the Rubik's Cube tool as a module.

This loader is safe to:
- import from a file on disk
- run from a shelf button
- paste into the Maya Script Editor

If Maya does not know where this project lives yet, the loader will prompt for
`rubiks_cube.py` once and then remember that directory in a Maya optionVar.
"""

import importlib
import os
import sys

import maya.cmds as cmds


def _bootstrap_loader_directory():
    candidates = []
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
        candidates.append(normalized_path)

    add_candidate(globals().get("__file__"))
    code_object = globals().get("_bootstrap_loader_directory").__code__
    add_candidate(code_object.co_filename)
    add_candidate(os.environ.get("RUBIKS_CUBE_TOOL_DIR"))
    add_candidate(os.getcwd())
    try:
        add_candidate(cmds.internalVar(userScriptDir=True))
    except Exception:
        pass

    for directory in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        add_candidate(directory)
    for directory in list(sys.path):
        add_candidate(directory)

    for directory in candidates:
        helper_module = os.path.join(directory, "rubiks_tool_paths.py")
        if os.path.exists(helper_module):
            if directory not in sys.path:
                sys.path.insert(0, directory)
            return directory
    return None


_bootstrap_loader_directory()

from rubiks_tool_paths import (
    RUBIKS_MODULE_NAMES,
    TOOL_DIRECTORY_OPTIONVAR,
    add_search_directory,
    ensure_directory_on_sys_path,
    is_tool_directory,
)


MODULE_NAME = "rubiks_cube"


def prompt_for_project_root():
    selection = cmds.fileDialog2(
        caption="Select rubiks_cube.py",
        fileFilter="Python Files (*.py)",
        fileMode=1,
        okCaption="Use Selected File",
    )
    if not selection:
        return None

    selected_file = selection[0]
    selected_directory = os.path.dirname(os.path.abspath(selected_file))
    if is_tool_directory(selected_directory):
        return selected_directory

    cmds.warning(
        "The selected directory does not contain the Maya tool module and rubiks_solver_core.py."
    )
    return None


def resolve_project_root(project_root=None, prompt_if_missing=True):
    candidates = []
    seen_directories = set()

    # Prefer explicit/saved locations first, then fall back to wherever Maya or
    # Python says this code might have come from.
    add_search_directory(candidates, seen_directories, project_root)
    add_search_directory(candidates, seen_directories, os.environ.get("RUBIKS_CUBE_TOOL_DIR"))
    try:
        if cmds.optionVar(exists=TOOL_DIRECTORY_OPTIONVAR):
            add_search_directory(
                candidates,
                seen_directories,
                cmds.optionVar(q=TOOL_DIRECTORY_OPTIONVAR),
            )
    except Exception:
        pass

    if "__file__" in globals():
        add_search_directory(candidates, seen_directories, __file__)
    add_search_directory(candidates, seen_directories, getattr(sys.modules.get(__name__), "__file__", None))
    code_object = getattr(resolve_project_root, "__code__", None)
    if code_object is not None:
        add_search_directory(candidates, seen_directories, code_object.co_filename)
    add_search_directory(candidates, seen_directories, os.getcwd())
    try:
        add_search_directory(candidates, seen_directories, cmds.internalVar(userScriptDir=True))
    except Exception:
        pass

    for path in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        add_search_directory(candidates, seen_directories, path)
    for path in sys.path:
        add_search_directory(candidates, seen_directories, path)

    for directory in candidates:
        if is_tool_directory(directory):
            return directory

    if prompt_if_missing:
        selected_directory = prompt_for_project_root()
        if selected_directory:
            return selected_directory

    raise RuntimeError(
        "Could not locate the Rubik's Cube tool directory. "
        "Set RUBIKS_CUBE_TOOL_DIR, save RubiksCubeToolDirectory, or choose the Maya tool file when prompted."
    )


def load(project_root=None, prompt_if_missing=True):
    resolved_project_root = resolve_project_root(
        project_root=project_root,
        prompt_if_missing=prompt_if_missing,
    )
    expected_module_path = os.path.normcase(
        os.path.abspath(os.path.join(resolved_project_root, MODULE_NAME + ".py"))
    )

    # Keep both the current Python session and future Maya sessions pointed at
    # the same tool directory so sibling modules import consistently.
    ensure_directory_on_sys_path(resolved_project_root)

    os.environ["RUBIKS_CUBE_TOOL_DIR"] = resolved_project_root
    try:
        cmds.optionVar(sv=(TOOL_DIRECTORY_OPTIONVAR, resolved_project_root))
    except Exception:
        pass

    modules_to_reset = (
        "rubiks_cube",
        "rubiks_move_notation",
        "rubiks_state_utils",
        "rubiks_tool_paths",
        "rubiks_solver_core",
    )
    existing_module = sys.modules.get(MODULE_NAME)
    existing_module_path = os.path.normcase(
        os.path.abspath(getattr(existing_module, "__file__", ""))
    ) if existing_module is not None else None

    # If Maya is still holding a module imported from the old folder, reload is
    # not enough because it would keep following the stale file path.
    if existing_module_path != expected_module_path:
        for module_name in modules_to_reset:
            sys.modules.pop(module_name, None)

    for legacy_module_name in RUBIKS_MODULE_NAMES:
        if legacy_module_name != MODULE_NAME and legacy_module_name in sys.modules:
            sys.modules.pop(legacy_module_name, None)

    existing_module = sys.modules.get(MODULE_NAME)
    if existing_module is None:
        return importlib.import_module(MODULE_NAME)
    return importlib.reload(existing_module)


load()
