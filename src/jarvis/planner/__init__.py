"""Planner module.

Breaks a natural-language request into an ordered list of steps before
anything executes, estimates the risk category of each step, and tracks
plan progress as steps succeed, fail, or get revised. Never executes
steps itself — execution belongs to the Tool Manager.
"""
