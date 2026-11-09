"""Local configuration management, intentionally separate from OPC runtimes."""

from .server import ManagementServer, make_server

__all__ = ["ManagementServer", "make_server"]
