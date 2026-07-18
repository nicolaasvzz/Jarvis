"""Notification System module.

Pushes events to the user: task started/finished, errors, approval
requests, and milestones of long-running tasks. Transport-agnostic so
delivery channels (phone push, etc.) can be added without touching the
callers.
"""
