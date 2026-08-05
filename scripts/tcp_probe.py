# Probe a given port.
def tcp_probe(host: str, port: int, timeout: int = 3) -> bool:
    """True if a TCP connection can be opened, False otherwise."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
