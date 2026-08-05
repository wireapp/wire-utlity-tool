# Resolve a DNS name to a list of IPs.
import socket
import logging
from typing import List, Tuple

log = logging.getLogger("wire-utility")

def resolve_name(name: str) -> List[str]:
    """
    Return every IPv4 address that the given DNS name resolves to.
    Empty list → name cannot be resolved.
    """
    try:
        addrinfo = socket.getaddrinfo(name, None, socket.AF_INET)
        ips = sorted({info[4][0] for info in addrinfo})
        log.debug(f"{name} resolves to {ips}")
        return ips
    except socket.gaierror as exc:
        log.error(f"Failed to resolve {name}: {exc}")
        return []
