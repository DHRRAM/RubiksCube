"""Helpers for locating and loading sibling Rubik's Cube modules."""

import importlib
import os
import sys


RUBIKS_MODULE_NAMES = ("rubiks_cube",)
SOLVER_MODULE_NAME = "rubiks_solver_core"
TOOL_DIRECTORY_OPTIONVAR = "RubiksCubeToolDirectory"


def add_search_directory(search_directories, seen_directories, path):
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


def find_module_file(module_name, search_directories):
    for directory in search_directories:
        candidate_path = os.path.join(directory, module_name + ".py")
        if os.path.exists(candidate_path):
            return candidate_path
    return None


def ensure_directory_on_sys_path(directory):
    if directory and directory not in sys.path:
        sys.path.insert(0, directory)


def is_tool_directory(path):
    if not path:
        return False

    normalized_path = os.path.abspath(path)
    if os.path.isfile(normalized_path):
        normalized_path = os.path.dirname(normalized_path)

    if not os.path.isdir(normalized_path):
        return False

    solver_exists = os.path.exists(os.path.join(normalized_path, SOLVER_MODULE_NAME + ".py"))
    maya_tool_exists = any(
        os.path.exists(os.path.join(normalized_path, module_name + ".py"))
        for module_name in RUBIKS_MODULE_NAMES
    )
    return solver_exists and maya_tool_exists


def load_module_from_file(module_name, module_path):
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(
            "Could not build an import spec for {0}".format(module_path)
        )

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module
