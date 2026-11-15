"""
AgentError hierarchy — unified error handling for agent execution.
"""


class AgentError(Exception):
    """Base exception for all agent errors."""
    code: str = "AGENT_ERROR"
    recoverable: bool = False

    def __init__(self, message: str = "", code: str = None, recoverable: bool = None):
        super().__init__(message)
        if code:
            self.code = code
        if recoverable is not None:
            self.recoverable = recoverable


class ProviderError(AgentError):
    """LLM provider error (timeout, rate limit, etc.)."""
    code = "PROVIDER_ERROR"
    recoverable = True

    def __init__(self, message: str, provider: str = "", status_code: int = 0):
        super().__init__(message, recoverable=status_code in (429, 500, 502, 503))
        self.provider = provider
        self.status_code = status_code


class ToolError(AgentError):
    """Tool execution failed — recoverable, LLM should try alternative."""
    code = "TOOL_ERROR"
    recoverable = True

    def __init__(self, tool: str, reason: str):
        super().__init__(f"Tool '{tool}' failed: {reason}")
        self.tool = tool
        self.reason = reason


def safe_tool_call(tool_name: str, fn, *args, **kwargs) -> str:
    """Execute a tool function safely, returning structured error on failure.

    Returns JSON string — either the tool's result or {"error": ..., "_tool_error": true}.
    This lets the SSE adapter and LLM handle failures gracefully.
    """
    import json as _json
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        err = ToolError(tool_name, str(e))
        return _json.dumps({"error": str(err), "code": err.code, "_tool_error": True}, ensure_ascii=False)
