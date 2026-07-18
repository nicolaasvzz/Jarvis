"""Tool Manager module.

Registry and dispatcher for every capability Jarvis has (file ops,
desktop control, browser, ...). Each tool declares a name, input schema,
and risk category; the Tool Manager enforces the security policy
(confirmation for dangerous categories) and logs every invocation before
delegating to the owning module.
"""
