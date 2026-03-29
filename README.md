# Rubik's Cube Rotation

A Rubik's Cube project built around a Maya tool and a standalone solver core.

Demo videos:

- [Solving demo (.mp4)](README_media/20260329_ProgressVid_Solving.mp4)
- [Aesthetics demo (.mp4)](README_media/20260329_ProgressVid_Aesthetics.mp4)

The repository is centered on two visible Python modules:

- `rubiks_cube.py` for the Maya scene tool, UI, cube manipulation, and solve integration
- `rubiks_solver_core.py` for the standalone logical cube model and solver

Supporting modules now keep shared logic and Maya bootstrapping smaller:

- `rubiks_move_notation.py` for shared move definitions, parsing, formatting, inversion, and scramble generation
- `rubiks_state_utils.py` for shared logical piece-state transforms and catalog builders
- `rubiks_tool_paths.py` for Maya-friendly sibling-module discovery and loading helpers
- `maya_loader.py` for bootstrapping the Maya tool module
- `solver_harness.py` for exercising the standalone solver outside Maya

## Overview

This project lets you:

- build a 3x3x3 Rubik's Cube in Maya
- turn faces and rotate the full cube
- scramble and solve the cube
- animate moves with keyframes
- interact with the cube through UI and viewport controls
- pause, step through, scrub, undo, redo, and replay move history
- style the cube with themes and presentation-focused appearance controls
- reuse the solver logic outside Maya through the standalone core

## Repository Structure

- `rubiks_cube.py`
  The main Maya tool. It handles cube creation, materials, transforms, UI, viewport controls, scrambling, playback/history, animation, and solve execution.
- `rubiks_solver_core.py`
  The standalone solver module. It mirrors the cube-state encoding used by the Maya tool and contains the two-phase search logic.
- `rubiks_move_notation.py`
  Shared notation helpers for parsing, formatting, inverses, and scramble text generation.
- `rubiks_state_utils.py`
  Shared logical state helpers and piece-state table builders used by both the Maya tool and the standalone solver.
- `rubiks_tool_paths.py`
  Helper functions that let Maya find and load sibling modules more reliably.
- `maya_loader.py`
  A small Maya bootstrap entry point for loading the main tool module.
- `solver_harness.py`
  A standalone script for exercising solver behavior outside Maya.

## Requirements

### Maya tool

To use `rubiks_cube.py`, you need Autodesk Maya with Python support.

Supported Maya versions: Autodesk Maya 2022 or newer.

Older Maya releases that are centered on Python 2 are not supported by this repository's current shared modules.

The script uses Maya APIs such as:

- `maya.cmds`
- `maya.utils`

It also supports Qt-based integrations when available:

- `PySide2`
- `shiboken2`
- `maya.OpenMayaUI`

Some viewport-control behavior is nicer when the Qt modules are available, but the core Maya functionality is still centered on Maya's Python environment.

For local regression coverage outside Maya, run:

`python -m unittest discover -s tests`

### Standalone solver

`rubiks_solver_core.py` uses only the Python standard library, so it can be worked on outside Maya as a normal Python module.

## Main Features

### In `rubiks_cube.py`

- builds the cube geometry in Maya
- assigns face colors/materials
- tracks cube orientation and logical state
- supports animated and non-animated move execution
- provides scramble, algorithm-run, and solve actions
- accepts algorithm text input and can export move history back into the UI
- adds playback controls for pause, step forward/backward, scrub, and undo/redo
- keeps history available through solves so playback can review pre-solve and solve moves together
- updates scramble, algorithm playback, and solve history live in the UI
- creates viewport arrow controls for direct interaction
- guards viewport-control clicks so context-menu or shader actions do not accidentally trigger turns
- supports saving and restoring a reset pose
- preserves pose/history/animation when visual settings rebuild the cube
- includes a Maya UI for both solving and visual settings
- offers theme presets, bevel controls, sticker/gap controls, viewport-control polish, and presentation toggles

### In `rubiks_solver_core.py`

- defines the logical Rubik's Cube state model
- supports standard face moves and search move expansions
- implements a two-phase IDA* solving approach
- uses move tables and pruning tables to reduce search cost
- is designed to stay compatible with the Maya tool's state encoding
- shares notation/state helpers with the Maya tool so move parsing stays consistent

## Maya Workflow

When loaded in Maya, `rubiks_cube.py` creates a controller window for the cube tool.

The Maya-side workflow is roughly:

1. Build or rebuild the cube.
2. Make moves through buttons or viewport controls.
3. Scramble the cube.
4. Solve from the current logical state.
5. Optionally animate the result with keyframes enabled.
6. Use playback controls to review the full move history, including the solve itself.

The tool also supports:

- whole-cube orientation changes
- reset-state capture
- algorithm text entry for running pasted move sequences
- exporting move history or inverse history into the algorithm field
- playback controls for move speed, pausing, stepping, undo/redo, and history scrubbing
- live history scrubber updates during scramble, algorithm playback, and solve execution
- clearing keyframes and returning to a saved pose
- ten curated theme presets
- bevel, gap, sticker, viewport-control, background, floor-grid, and presentation appearance controls
- visual rebuilds that try to preserve the current cube animation and playback state
- animated scramble behavior that temporarily suspends Maya cached playback to reduce cache-memory warnings

## Solver Workflow

`rubiks_solver_core.py` exists so the solving logic can be developed separately from the Maya scene code.

At a high level, the solver:

- represents corners and edges as encoded piece states
- applies legal cube moves to those states
- uses a two-phase search strategy
- expands compact solver moves into executable quarter turns when needed

This separation makes it easier to iterate on solving logic without mixing every change into the Maya UI layer.

## Solver Design Notes

The solver core uses:

- standard face moves like `U`, `D`, `R`, `L`, `F`, `B`
- inverse turns like `U'` and `R'`
- search-time double turns like `U2`, `R2`, and similar moves

The Maya tool keeps track of cube orientation separately so world-facing controls can still map back to the correct logical move.

## Practical Notes

- `rubiks_cube.py` is the user-facing Maya script.
- `rubiks_solver_core.py` is the cleaner place to work on search logic and state encoding.
- Shared move/state helpers now live in small pure-Python modules so the Maya tool and standalone solver do not have to duplicate that logic.
- The Maya UI now has a stronger split between solve/playback tools and aesthetics/presentation controls.
- Playback history is now treated as a session timeline rather than a throwaway solve buffer.
- The Maya script is still responsible for scene behavior, UI, and Maya callbacks, so it remains the more stateful entry point.
- The solver core is intended to stay in sync with the Maya script's cube representation.

## Caveats

- The Maya tool and the standalone solver core need to stay compatible with each other.
- The Maya script has import-time side effects because it behaves like an interactive tool, not just a passive library module.
- Some scene interaction details depend on Maya-specific and Qt-specific behavior.

## Future Improvements

- Add support for different sized cubes

## License

No license file is currently included in the repository.
