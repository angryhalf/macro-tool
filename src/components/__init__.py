"""Feature components of the macro tool.

Each sub-package is one feature (``macro``, ``playback``, ``input``,
``screen_watch``, ``capture``, ``ui``); thin UI modules that belong to no
single feature (the tabs and editor dialogs) live directly in this package.
Shared, cross-feature code belongs in :mod:`common` instead.
"""
