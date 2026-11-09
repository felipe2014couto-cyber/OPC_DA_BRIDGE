"""Opt-in, bounded single-flight JSON transport over one verified SSH session.

No automatic retry: loss of an acknowledgement can mean the runtime applied it.
The caller's existing source-session/sequence protocol handles reconciliation.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import select
import subprocess
import threading
import time
import uuid


class StreamError(RuntimeError):
    pass


class JsonLineProcess:
    LIMIT = 2097152

    def __init__(self, command, timeout=12, max_requests=64, max_age_s=60):
        self.command = command
        self.timeout = timeout
        self.max_requests = max_requests
        self.max_age_s = max_age_s
        self.lock = threading.Lock()
        self.process = None
        self.created = 0
        self.requests = 0

    def _close(self):
        process, self.process = self.process, None
        if process is None:
            return
        # Closing stdin lets the remote agent exit normally; kill only this
        # owned local transport if it does not exit, never a Windows server.
        process.stdin.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        process.stdout.close()

    def close(self):
        with self.lock:
            self._close()

    def request(self, payload):
        serialization = time.monotonic()
        request_id = str(uuid.uuid4())
        body = json.dumps({"request_id": request_id, "payload": payload},
                          ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
        if len(body) > self.LIMIT:
            raise StreamError("Pedido excede o limite do transporte persistente.")
        serialization_ms = (time.monotonic() - serialization) * 1000
        queued = time.monotonic()
        with self.lock:
            started = time.monotonic()
            spawn_ms = 0.0
            reused = bool(self.process and self.process.poll() is None
                          and self.requests < self.max_requests and started - self.created < self.max_age_s)
            try:
                if not reused:
                    self._close()
                    spawning = time.monotonic()
                    self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                    bufsize=0)
                    spawn_ms = (time.monotonic() - spawning) * 1000
                    self.created = time.monotonic()
                    self.requests = 0
                    os.set_blocking(self.process.stdin.fileno(), False)
                    os.set_blocking(self.process.stdout.fileno(), False)
                deadline = time.monotonic() + self.timeout
                sent = 0
                response = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    readable, writable, _ = select.select([self.process.stdout],
                        [self.process.stdin] if sent < len(body) else [], [], remaining)
                    if writable:
                        sent += os.write(self.process.stdin.fileno(), body[sent:])
                    if readable:
                        chunk = os.read(self.process.stdout.fileno(), 65536)
                        if not chunk:
                            raise EOFError()
                        response.extend(chunk)
                        if len(response) > self.LIMIT:
                            raise ValueError()
                        if b"\n" in response:
                            line, rest = bytes(response).split(b"\n", 1)
                            if rest.strip() or sent != len(body):
                                raise ValueError()
                            message = json.loads(line.decode("utf-8-sig"))
                            if message.get("request_id") != request_id or not isinstance(message.get("reply"), dict):
                                raise ValueError()
                            self.requests += 1
                            return message["reply"], {"reused": reused,
                                "serialization_ms": serialization_ms,
                                "queue_ms": (started - queued) * 1000,
                                "local_ssh_spawn_ms": spawn_ms,
                                "roundtrip_ms": (time.monotonic() - started) * 1000}
            except (OSError, ValueError, TypeError, AttributeError, EOFError, subprocess.SubprocessError):
                self._close()
                raise StreamError("Transporte persistente sem confirmação; a operação não foi repetida.") from None
