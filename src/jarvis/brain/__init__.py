"""Brain module.

Owns the connection to the language model (Anthropic API) and turns
conversation state into decisions: what to say, which tool to call next,
when the task is complete. Contains no tool implementations and no I/O
besides the LLM call itself.
"""
