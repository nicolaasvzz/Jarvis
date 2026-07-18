"""Authentication & permissions module.

Verifies the phone client's credentials for the API server and implements
the permission model: safe actions run freely, dangerous action
categories (delete, send email, install, system settings, spending,
elevated shell) require explicit user confirmation. Every decision is
logged.
"""
