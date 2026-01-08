"""MCP error classes for DB server."""


class MCPError(Exception):
    """MCP protocol error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(self.message)
