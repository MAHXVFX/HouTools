"""Tool modules.

Convention: every tool is a module houtools.tools.<tool_id> exposing run().
The dispatcher imports by id, so there is no registry to desynchronise
during hot reloads - adding a tool means adding a module plus a menu
item in MainMenuCommon.xml (the latter needs a Houdini restart).
"""
