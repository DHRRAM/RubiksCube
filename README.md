# Rubik's Cube Rotation

A Rubik's Cube project built around a Maya tool and a standalone solver core.

![Rubik's Cube solver demo](README_media/20260328_solverImplemented.gif)

The repository is centered on two visible Python modules:

- `rubiks_cube.py` for the Maya scene tool, UI, cube manipulation, and solve integration
- `rubiks_solver_core.py` for the standalone logical cube model and solver

## Overview

This project lets you:

- build a 3x3x3 Rubik's Cube in Maya
- turn faces and rotate the full cube
- scramble and solve the cube
- animate moves with keyframes
- interact with the cube through UI and viewport controls
- reuse the solver logic outside Maya through the standalone core

## Repository Structure

- `rubiks_cube.py`
  The main Maya tool. It handles cube creation, materials, transforms, UI, viewport controls, scrambling, animation, and solve execution.
- `rubiks_solver_core.py`
  The standalone solver module. It mirrors the cube-state encoding used by the Maya tool and contains the two-phase search logic.

## Requirements

### Maya tool

To use `rubiks_cube.py`, you need Autodesk Maya with Python support.

The script uses Maya APIs such as:

- `maya.cmds`
- `maya.utils`

It also supports Qt-based integrations when available:

- `PySide2`
- `shiboken2`
- `maya.OpenMayaUI`

Some viewport-control behavior is nicer when the Qt modules are available, but the core Maya functionality is still centered on Maya's Python environment.

### Standalone solver

`rubiks_solver_core.py` uses only the Python standard library, so it can be worked on outside Maya as a normal Python module.

## Main Features

### In `rubiks_cube.py`

- builds the cube geometry in Maya
- assigns face colors/materials
- tracks cube orientation and logical state
- supports animated and non-animated move execution
- provides scramble and solve actions
- creates viewport arrow controls for direct interaction
- supports saving and restoring a reset pose
- includes a Maya UI for both solving and visual settings

### In `rubiks_solver_core.py`

- defines the logical Rubik's Cube state model
- supports standard face moves and search move expansions
- implements a two-phase IDA* solving approach
- uses move tables and pruning tables to reduce search cost
- is designed to stay compatible with the Maya tool's state encoding

## Maya Workflow

When loaded in Maya, `rubiks_cube.py` creates a controller window for the cube tool.

The Maya-side workflow is roughly:

1. Build or rebuild the cube.
2. Make moves through buttons or viewport controls.
3. Scramble the cube.
4. Solve from the current logical state.
5. Optionally animate the result with keyframes enabled.

The tool also supports:

- whole-cube orientation changes
- reset-state capture
- clearing keyframes and returning to a saved pose
- bevel and shader-related appearance controls

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
- The Maya script is responsible for both scene behavior and solver integration, so it is naturally larger and more stateful.
- The solver core is intended to stay in sync with the Maya script's cube representation.

## Caveats

- The Maya tool and the standalone solver core need to stay compatible with each other.
- The Maya script has import-time side effects because it behaves like an interactive tool, not just a passive library module.
- Some scene interaction details depend on Maya-specific and Qt-specific behavior.

## Future Improvements

- reduce duplicated logic between the Maya tool and standalone solver
- split Maya-specific responsibilities into smaller modules
- add regression tests around move mapping and state compatibility
- document supported Maya versions explicitly

## License

No license file is currently included in the repository.
