"""Management entry point; optional systemd installation is a separate step."""
import argparse
import os
import sys
from pathlib import Path

from .server import make_server
from .store import ConfigurationError


def main():
    parser = argparse.ArgumentParser(description="OPC Bridge — gerenciamento local com agente simulado")
    parser.add_argument("--host", default="127.0.0.1", help="Apenas loopback nesta etapa")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-dir", type=Path, default=Path(".opc-bridge-management"))
    parser.add_argument(
        "--public-origin", default=os.environ.get("OPC_BRIDGE_PUBLIC_ORIGIN"),
        help="Origem HTTP/HTTPS exata do proxy autenticado confiável (host e porta explícitos); "
             "também configurável por OPC_BRIDGE_PUBLIC_ORIGIN")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve estar entre 1 e 65535.")
    try:
        server = make_server(args.data_dir, args.host, args.port, public_origin=args.public_origin)
    except (ConfigurationError, OSError) as exc:
        print("Não foi possível iniciar o gerenciamento: %s" % exc, file=sys.stderr)
        return 1
    print("OPC Bridge — http://127.0.0.1:%d — SIMULAÇÃO / OPC DA AUTORIZADO; confira a aplicação Windows no painel" % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
