"""Reusable, feature-agnostic Qt widgets shared across the UI layer.

Leaf tier of the component tree: nothing here knows about macros or
conditions.  Feature tabs and editor dialogs import *from* this package;
the reverse never happens (see ``skills/project-structure/SKILL.md``).
"""
