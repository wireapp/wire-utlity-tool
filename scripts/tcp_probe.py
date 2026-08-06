"""
tcp_probe – lightweight TCP port probe.

If the Python ``socket`` module is available we use it; otherwise we fall back
to the external ``nc`` (netcat) command, which is present in almost every
Linux container image.
"""

import logging
import subprocess

logger = logging.getLogger("wire-utility.tcp_probe")

def _socket_probe(host: str, port: int, timeout: int) -> bool:
    try:
        import socket
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception as exc:      # includes ImportError, OSError, …
        logger.debug(f"socket probe failed ({host}:{port}) – {exc!r}")
        return False

def _nc_probe(host: str, port: int, timeout: int) -> bool:
    try:
        # nc -z -w <seconds> host port
        result = subprocess.run(
            ["nc", "-z", "-w", str(timeout), host, str(port), "-q", "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except FileNotFoundError:
        logger.error("Netcat (nc) not found – cannot perform TCP probe.")
        return False
    except Exception as exc:
        logger.debug(f"nc probe failed ({host}:{port}) – {exc!r}")
        return False

def tcp_probe(host: str, port: int, timeout: int = 3) -> bool:
    """
    Public entry point – prefers the pure‑Python socket probe, falls back
    to netcat when the socket module cannot be imported.
    """
    if _socket_probe(host, port, timeout):
        return True
    return _nc_probe(host, port, timeout)
