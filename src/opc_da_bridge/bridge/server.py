"""Synchronous loopback TCP endpoint used by the separate Windows Mirror process."""
from __future__ import annotations

import socketserver
from typing import Any

from opc_da_bridge.bridge.json_protocol import DEMO_ITEMS, decode_request, encode_message, record_from_cache
from opc_da_bridge.core.cache import BoundedLatestValueCache


class CacheBridgeServer:
    """Expose bounded defensive cache snapshots on 127.0.0.1 only."""
    def __init__(self, cache: BoundedLatestValueCache, host: str = "127.0.0.1", port: int = 45871):
        if host != "127.0.0.1":
            raise ValueError("cache bridge must bind to IPv4 loopback 127.0.0.1")
        self.cache = cache
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline(1_048_577)
                if len(line) > 1_048_576:
                    return
                try:
                    request = decode_request(line)
                    requested = request.get("items")
                    names = requested if isinstance(requested, list) else list(DEMO_ITEMS)
                    paths = [DEMO_ITEMS.get(name, (name, ""))[0] for name in names if isinstance(name, str)]
                    cache_snapshot = owner.cache.snapshot_batch(paths)
                    entries: list[dict[str, Any]] = []
                    for name in names:
                        if not isinstance(name, str):
                            continue
                        path = DEMO_ITEMS.get(name, (name, ""))[0]
                        item = cache_snapshot.get(path)
                        if item is not None:
                            entries.append(record_from_cache(name, item))
                    self.wfile.write(encode_message({"op": "snapshot", "protocol_version": 1, "items": entries}))
                except (ValueError, PermissionError):
                    self.wfile.write(b'{"error":"invalid_or_forbidden_request"}\n')

        self._server = socketserver.ThreadingTCPServer((host, port), Handler, bind_and_activate=False)
        self._server.allow_reuse_address = True
        self._server.daemon_threads = True
        self._server.server_bind()
        self._server.server_activate()

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
