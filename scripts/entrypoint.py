from __future__ import annotations
import filecmp
import getpass
import os
import shutil
import sys
import threading
import time
import subprocess
import logging
from pathlib import Path

#  dns_util.py – provides resolve_name(name) → List[str]
#  probe.py   – provides tcp_probe(host, port, timeout=3) → bool
from .dns_utils import resolve_name
from .tcp_probe import tcp_probe

import argparse

from datetime import datetime

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("wire-utility")

# Environment variable constants
MINIO_SERVICE_ENDPOINT = os.getenv('MINIO_SERVICE_ENDPOINT', '')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', '')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', '')

CASSANDRA_SERVICE_NAME = os.getenv('CASSANDRA_SERVICE_NAME', '')
CASSANDRA_SERVICE_PORT = int(os.getenv('CASSANDRA_SERVICE_PORT')) if os.getenv('CASSANDRA_SERVICE_PORT') else None

# only import these if there is a cassandra to test.
try:
    from cassandra.cluster  import Cluster, ExecutionProfile, EXEC_PROFILE_DEFAULT
    from cassandra.policies import RoundRobinPolicy
except ImportError:
    Cluster = None
    logger.warning("Cassandra python libraries not available; skipping cassandra tests.")

try:
    import socket
except ImportError:
    socket = None
    logger.warning("Socket python libraries not available; skipping connectivity tests.")

RABBITMQ_SERVICE_NAME = os.getenv('RABBITMQ_SERVICE_NAME', '')
RABBITMQ_SERVICE_PORT = int(os.getenv('RABBITMQ_SERVICE_PORT')) if os.getenv('RABBITMQ_SERVICE_PORT') else None
RABBITMQ_MGMT_PORT = int(os.getenv('RABBITMQ_MGMT_PORT')) if os.getenv('RABBITMQ_MGMT_PORT') else None
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME', '')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD', '')

ES_SERVICE_NAME = os.getenv('ES_SERVICE_NAME', '')
ES_PORT = int(os.getenv('ES_PORT')) if os.getenv('ES_PORT') else None
PGHOST = os.getenv('PGHOST', '')
PGPORT = int(os.getenv('PGPORT')) if os.getenv('PGPORT') else None
PGUSER = os.getenv('PGUSER', '')
PGDATABASE = os.getenv('PGDATABASE', '')

ENABLE_PROBE_THREAD = os.getenv('ENABLE_PROBE_THREAD', 'false').lower() == 'true'

EXPECTED_USER = "nonroot"

def run_command(command, capture_output=True, check=False):
    """Run shell command"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=capture_output,
            text=True,
            check=check
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError:
        return False, "", ""

def parse_minio_endpoint(endpoint):
    """Parse MinIO endpoint to extract host and port"""
    if not endpoint:
        return None, None
    endpoint = endpoint.replace('http://', '').replace('https://', '')
    if ':' in endpoint:
        host, port = endpoint.split(':', 1)
        return host, int(port)
    else:
        return endpoint, 80

def check_service(host, port, name):
    """Check if a service is reachable – now delegated to the ``tcp_probe`` helper."""
    # Use the shared helper; it already contains a socket fallback.
    ok = tcp_probe(host, port, timeout=3)
    if ok:
        logger.info(f"{name} ({host}:{port}) is reachable")
    else:
        logger.error(f"{name} ({host}:{port}) is unreachable")
        # Optional DNS‑resolution logging (guarded if socket module is missing)
        try:
            ip = socket.gethostbyname(host) if socket else host
            logger.info(f"Resolved {name} to IP: {ip}")
        except Exception as e:
            logger.error(f"Failed to resolve {name} host: {e}")
    return ok

def check_service_health(url):
    """Check if an HTTP service is reachable"""
    try:
        response = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
            check=False
        )
        status_code = response.stdout.strip()
        if status_code.startswith("2"):
            logger.info(f"HTTP service {url} is reachable (status code: {status_code})")
            return True
        else:
            logger.error(f"HTTP service {url} returned status code {status_code}")
            return False
    except Exception as e:
        logger.error(f"Failed to reach HTTP service {url}: {e}")
        return False

def configure_minio():
    """Configure MinIO client"""
    endpoint = os.getenv('MINIO_SERVICE_ENDPOINT', '')
    access_key = os.getenv('MINIO_ACCESS_KEY', '')
    secret_key = os.getenv('MINIO_SECRET_KEY', '')

    if not all([endpoint, access_key, secret_key]):
        logger.warning("MinIO configuration incomplete - skipping")
        return False

    host, port = parse_minio_endpoint(endpoint)
    if not host or not check_service(host, port, "MinIO"):
        logger.error("MinIO unreachable - skipping configuration")
        return False

    cmd = f"mc alias set wire-minio {endpoint} {access_key} {secret_key}"
    success, _, _ = run_command(cmd, capture_output=True)

    if success:
        logger.info("MinIO client configured")
    else:
        logger.error("MinIO client configuration failed")

    return success

def configure_cassandra():
    """Configure Cassandra client"""
    service_name = os.getenv('CASSANDRA_SERVICE_NAME', 'cassandra')
    service_port = os.getenv('CASSANDRA_SERVICE_PORT', '9042')

    home_dir = Path.home()
    cassandra_dir = home_dir / '.cassandra'
    cassandra_dir.mkdir(exist_ok=True)

    config_content = f"""[connection]
hostname = {service_name}
port = {service_port}
"""

    config_file = cassandra_dir / 'cqlshrc'
    config_file.write_text(config_content)
    logger.info("Cassandra client configured")

def configure_rabbitmq():
    """Configure RabbitMQ admin"""
    service_name = os.getenv('RABBITMQ_SERVICE_NAME', 'rabbitmq')
    mgmt_port = os.getenv('RABBITMQ_MGMT_PORT', '15672')
    username = os.getenv('RABBITMQ_USERNAME', 'guest')
    password = os.getenv('RABBITMQ_PASSWORD', 'guest')

    config_content = f"""[default]
hostname = {service_name}
port = {mgmt_port}
username = {username}
password = {password}
"""

    config_file = Path.home() / '.rabbitmqadmin.conf'
    config_file.write_text(config_content)
    logger.info("RabbitMQ admin configured")

def create_status_script():
    """Create status checking script with emoji output"""
    status_script = '''#!/bin/bash
set -e

echo "=== Wire Utility Pod Status ==="
echo "Pod: $(hostname)"
echo "Time: $(date)"
echo ""

# Extract MinIO details
MINIO_HOST=$(echo ${MINIO_SERVICE_ENDPOINT} | sed 's|http[s]*://||' | cut -d':' -f1)
MINIO_PORT=$(echo ${MINIO_SERVICE_ENDPOINT} | sed 's|http[s]*://||' | cut -d':' -f2)

echo "=== Connectivity ==="
timeout 2 nc -z $MINIO_HOST $MINIO_PORT >/dev/null 2>&1 && echo "✅ MinIO        $MINIO_HOST:$MINIO_PORT" || echo "❌ MinIO        $MINIO_HOST:$MINIO_PORT"
timeout 2 nc -z ${CASSANDRA_SERVICE_NAME} ${CASSANDRA_SERVICE_PORT} >/dev/null 2>&1 && echo "✅ Cassandra    ${CASSANDRA_SERVICE_NAME}:${CASSANDRA_SERVICE_PORT}" || echo "❌ Cassandra    ${CASSANDRA_SERVICE_NAME}:${CASSANDRA_SERVICE_PORT}"
timeout 2 nc -z ${RABBITMQ_SERVICE_NAME} ${RABBITMQ_SERVICE_PORT} >/dev/null 2>&1 && echo "✅ RabbitMQ     ${RABBITMQ_SERVICE_NAME}:${RABBITMQ_SERVICE_PORT}" || echo "❌ RabbitMQ     ${RABBITMQ_SERVICE_NAME}:${RABBITMQ_SERVICE_PORT}"
timeout 2 nc -z ${ES_SERVICE_NAME} ${ES_PORT} >/dev/null 2>&1 && echo "✅ Elasticsearch ${ES_SERVICE_NAME}:${ES_PORT}" || echo "❌ Elasticsearch ${ES_SERVICE_NAME}:${ES_PORT}"
timeout 2 nc -z ${PGHOST} ${PGPORT} >/dev/null 2>&1 && echo "✅ PostgreSQL   ${PGHOST}:${PGPORT}" || echo "❌ PostgreSQL   ${PGHOST}:${PGPORT}"
echo ""
echo "=== Quick Commands ==="
echo "status                    # Show this status"
echo "mc ls wire-minio          # List MinIO buckets"
echo "mc admin info wire-minio  # Show MinIO server info"
echo "cqlsh                     # Connect to Cassandra"
echo "rabbitmqadmin list queues # List RabbitMQ queues"
echo "psql                      # Connect to PostgreSQL"
echo "es usages                 # Show all available Elasticsearch debug commands"
echo "es all                    # Run all Elasticsearch diagnostics (health, nodes, indices, etc.)"
echo ""
'''
    status_file = Path('/tmp/status.sh')
    status_file.write_text(status_script)
    status_file.chmod(0o755)

def create_bashrc():
    """Create custom bashrc with emoji welcome and status alias"""
    bashrc_content = '''# Source system bashrc
[ -f /etc/bash.bashrc ] && source /etc/bash.bashrc

# Custom prompt
export PS1='\\[\\033[01;32m\\]\\u@wire-utility\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '

# Useful aliases
alias status='/tmp/status.sh'
alias status-full='python3 /opt/wire-utility/scripts/entrypoint.py status-full'
alias ll='ls -alF'
alias la='ls -A'

# Show welcome message on login
echo ""
echo "🔧 Welcome to Wire Utility Debug Pod"
echo "📊 Type 'status' to check service connectivity"
echo ""
/tmp/status.sh
'''
    bashrc_file = Path.home() / '.bashrc'
    timestamp = datetime.now().strftime('%Y%m%d-%s')
    bashrc_backup = Path.home() / f'.bashrc.{timestamp}'

    if bashrc_file.exists():
        shutil.move(str(bashrc_file), str(bashrc_backup))

    bashrc_file.write_text(bashrc_content)

    if bashrc_backup.exists():
        if filecmp.cmp(bashrc_file, bashrc_backup, shallow=False):
            bashrc_backup.unlink() # Delete an identical backup

def check_all_services():
    """Check connectivity to all services"""
    logger.info("Checking Service Connectivity")

    # MinIO
    minio_status = False
    if MINIO_SERVICE_ENDPOINT:
        minio_host, minio_port = parse_minio_endpoint(MINIO_SERVICE_ENDPOINT)
        if minio_host and minio_port:
            minio_status = check_service(minio_host, minio_port, "MinIO")
    else:
        logger.info("Skipping minio service test; no service defined.")

    # Cassandra
    cassandra_status = False
    if CASSANDRA_SERVICE_NAME and CASSANDRA_SERVICE_PORT:
        cassandra_status = check_service(CASSANDRA_SERVICE_NAME, CASSANDRA_SERVICE_PORT, "Cassandra")
    else:
        logger.info("Skipping cassandra service test; no service defined.")

    # RabbitMQ
    rabbitmq_status = False
    if RABBITMQ_SERVICE_NAME and RABBITMQ_SERVICE_PORT:
        rabbitmq_status = check_service(RABBITMQ_SERVICE_NAME, RABBITMQ_SERVICE_PORT, "RabbitMQ")
    else:
        logger.info("Skipping rabbitMQ service test; no service defined.")

    # Elasticsearch
    es_status = False
    if ES_SERVICE_NAME and ES_PORT:
        es_status = check_service(ES_SERVICE_NAME, ES_PORT, "Elasticsearch")
    else:
        logger.info("Skipping elasticsearch service test; no service defined.")

    # PostgreSQL
    pg_status = False
    if PGHOST and PGPORT:
        pg_status = check_service(PGHOST, PGPORT, "PostgreSQL")
    else:
        logger.info("Skipping PostgreSQL service test; no service defined.")

    return minio_status, cassandra_status, rabbitmq_status, es_status, pg_status

def _print_row(service: str, ip: str, port: int, ok: bool) -> None:
    """One‑line printer used by status_full – keeps the same emoji style."""
    mark = "✅" if ok else "❌"
    print(f"{mark} {service:<13} {ip}:{port}")

def check_cassandra_health(host, port, username=None, password=None):
    """Check Cassandra health using cassandra-driver with execution profiles."""
    if Cluster is None:
        logger.info("Cassandra driver unavailable – skipping Cassandra health check.")
        return False
    try:
        host = host or CASSANDRA_SERVICE_NAME
        port = port or CASSANDRA_SERVICE_PORT
        profile = ExecutionProfile(load_balancing_policy=RoundRobinPolicy())
        cluster = Cluster(
            [host],
            port=port,
            protocol_version=4,
            execution_profiles={EXEC_PROFILE_DEFAULT: profile}
        )
        session = cluster.connect()
        session.execute("SELECT now() FROM system.local")
        logger.info(f"Cassandra ({host}:{port}) is healthy (CQL query succeeded)")
        cluster.shutdown()
        return True
    except Exception as e:
        logger.error(f"Cassandra ({host}:{port}) health check failed: {e}")
        return False

def check_rabbitmq_service_health(url, username=None, password=None):
    """Check RabbitMQ HTTP service health with Basic Auth"""
    try:
        curl_cmd = [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url
        ]
        if username and password:
            curl_cmd.extend(["-u", f"{username}:{password}"])
        response = subprocess.run(
            curl_cmd,
            capture_output=True,
            text=True,
            check=False
        )
        status_code = response.stdout.strip()
        if status_code.startswith("2"):
            logger.info(f"RabbitMQ HTTP service {url} is reachable (status code: {status_code})")
            return True
        else:
            logger.error(f"RabbitMQ HTTP service {url} returned status code {status_code}")
            return False
    except Exception as e:
        logger.error(f"Failed to reach RabbitMQ HTTP service {url}: {e}")
        return False

def check_rabbitmq_running_nodes(url, username=None, password=None):
    """Get the number of running RabbitMQ nodes via management API and log node details"""
    try:
        curl_cmd = [
            "curl", "-s", url
        ]
        if username and password:
            curl_cmd.extend(["-u", f"{username}:{password}"])
        response = subprocess.run(
            curl_cmd,
            capture_output=True,
            text=True,
            check=False
        )
        import json
        nodes_info = json.loads(response.stdout)
        node_statuses = [
            {"name": node.get("name"), "running": node.get("running", False)}
            for node in nodes_info
        ]
        running_nodes = [node for node in node_statuses if node["running"]]
        logger.info(f"RabbitMQ nodes: {node_statuses}")
        logger.info(f"RabbitMQ running nodes: {len(running_nodes)}")
        return node_statuses
    except Exception as e:
        logger.error(f"Failed to get RabbitMQ running nodes: {e}")
        return []

def check_postgresql_connection(host, port, username, database):
    """Test PostgreSQL connection"""
    try:
        cmd = f"psql -h {host} -p {port} -U {username} -d {database} -c 'SELECT version();' --no-password"
        success, stdout, stderr = run_command(cmd, capture_output=True)
        if success:
            logger.info(f"PostgreSQL connection successful: {stdout.strip()}")
        else:
            logger.error(f"PostgreSQL connection failed: {stderr}")
        return success
    except Exception as e:
        logger.error(f"PostgreSQL connection test failed: {e}")
        return False

def cassandra_version(host: str, port: int) -> str | None:
    """
    Return the Cassandra release version string for the node at ``host:port``.
    * Returns ``None`` when the version cannot be obtained (node unreachable,
      driver missing, cqlsh not installed, etc.).
    """
    # ---- Use cqlsh CLI ----
    # Build a minimal cqlsh command that asks for the release_version.
    # ``-e`` executes the statement and exits; ``-u``/``-p`` are omitted because
    # the utility already knows the connectivity (or the node is open).
    cmd = f"cqlsh {host} {port} -e \"SELECT release_version FROM system.local\""
    try:
        # capture_output=True gives us both stdout and stderr.
        completed = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            logger.error(f"cqlsh failed for {host}:{port}: {completed.stderr.strip()}")
            return None

        # The output of ``cqlsh -e "SELECT release_version FROM system.local"`` looks like:
        # release_version
        # -----------------
        # 3.11.10
        # (1 rows)
        # We strip the lines and grab the second non‑empty line.
        lines = [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return lines[1]          # the version string
    except Exception as e:           # pragma: no cover – defensive
        logger.error(f"Failed to run cqlsh for {host}:{port}: {e}")

    return None

def show_versions() -> dict:
    """
    Query each configured service for its version string.
    Returns a dictionary of the form:
        {
            "cassandra": { "ip:port": "3.11.10", ... },
            "minio":      "MINIO_VERSION",
            "elasticsearch": "7.17.5",
            "postgresql": "PostgreSQL 13.5 (Ubuntu 13.5-1.pgdg20.04+1)",
            "rabbitmq":  "RabbitMQ 3.9.13"
        }
    Services that are not configured or whose version cannot be obtained are omitted.
    """
    versions = {}

    # ---------- MINIO ----------
    if MINIO_SERVICE_ENDPOINT:
        host, port = parse_minio_endpoint(MINIO_SERVICE_ENDPOINT)
        # The MinIO client (`mc`) can report the server version via `mc admin info`.
        # We keep it simple – just issue the HTTP health endpoint which returns a JSON with a `version` field.
        # (If you want a richer output you can call `mc admin info` instead.)
        try:
            import json
            out = subprocess.run(
                ["curl", "-s", f"http://{host}:{port}/minio/health/cluster"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            data = json.loads(out) if out else {}
            if "version" in data:
                versions["minio"] = data["version"]
        except Exception:
            pass   # keep silent – version will be omitted

    # ---------- CASSANDRA ----------
    if CASSANDRA_SERVICE_NAME and CASSANDRA_SERVICE_PORT:
        cass_versions = {}
        for ip in resolve_name(CASSANDRA_SERVICE_NAME):
            ver = cassandra_version(ip, CASSANDRA_SERVICE_PORT)
            if ver:
                cass_versions[f"{ip}:{CASSANDRA_SERVICE_PORT}"] = ver
        if cass_versions:
            versions["cassandra"] = cass_versions

    # ---------- RABBITMQ ----------
    if RABBITMQ_SERVICE_NAME and RABBITMQ_MGMT_PORT:
        try:
            out = subprocess.run(
                ["curl", "-s", f"http://{RABBITMQ_SERVICE_NAME}:{RABBITMQ_MGMT_PORT}/api/overview"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            import json
            data = json.loads(out) if out else {}
            if "rabbitmq_version" in data:
                versions["rabbitmq"] = data["rabbitmq_version"]
        except Exception:
            pass

    # ---------- ELASTICSEARCH ----------
    if ES_SERVICE_NAME and ES_PORT:
        try:
            out = subprocess.run(
                ["curl", "-s", f"http://{ES_SERVICE_NAME}:{ES_PORT}"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            # Elasticsearch returns JSON with a `version` dict.
            import json
            data = json.loads(out) if out else {}
            if "version" in data and "number" in data["version"]:
                versions["elasticsearch"] = data["version"]["number"]
        except Exception:
            pass

    # ---------- POSTGRESQL ----------
    if PGHOST and PGPORT:
        try:
            # Run a simple `SELECT version();` via psql.
            cmd = f'psql -h {PGHOST} -p {PGPORT} -U {PGUSER} -d {PGDATABASE} -c "SELECT version();" --no-password -t -A'
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                versions["postgresql"] = result.stdout.strip()
        except Exception:
            pass

    return versions

def status_full() -> bool:
    """
    Iterate over **all** IPs for each external service, probe TCP and
    run the service‑specific client check.

    Returns True only if **every** probe succeeded.
    """
    overall_ok = True

    # ---- MINIO -------------------------------------------------
    if MINIO_SERVICE_ENDPOINT:
        minio_host, minio_port = parse_minio_endpoint(MINIO_SERVICE_ENDPOINT)
        for ip in resolve_name(minio_host):
            ok = tcp_probe(ip, minio_port)
            if ok:
                # reuse the existing client‑check – we force the alias to the IP
                alias_cmd = f"mc alias set preflight-minio http://{ip}:{minio_port} {MINIO_ACCESS_KEY} {MINIO_SECRET_KEY}"
                ok = run_command(alias_cmd)[0] and run_command("mc ls preflight-minio")[0]
            _print_row("MinIO", ip, minio_port, ok)
            overall_ok = overall_ok and ok
    else:
        logger.info("Skipping minio service test; no service defined.")

    # ---- CASSANDRA ---------------------------------------------
    if CASSANDRA_SERVICE_NAME and CASSANDRA_SERVICE_PORT:
        for ip in resolve_name(CASSANDRA_SERVICE_NAME):
            ok = tcp_probe(ip, CASSANDRA_SERVICE_PORT) and check_cassandra_health(ip, CASSANDRA_SERVICE_PORT)
            _print_row("Cassandra", ip, CASSANDRA_SERVICE_PORT, ok)
            overall_ok = overall_ok and ok
    else:
        logger.info("Skipping cassandra service test; no service defined.")

    # ---- RABBITMQ ----------------------------------------------
    if RABBITMQ_SERVICE_NAME and RABBITMQ_SERVICE_PORT:
        for ip in resolve_name(RABBITMQ_SERVICE_NAME):
            ok = tcp_probe(ip, RABBITMQ_SERVICE_PORT)
            if ok and RABBITMQ_MGMT_PORT:
                # health‑check via the management API
                mgmt_url = f"http://{ip}:{RABBITMQ_MGMT_PORT}/api/overview"
                ok = check_rabbitmq_service_health(mgmt_url, RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
            else:
                logger.info("Skipping rabbitMQ management port test; no management port defined.")
            _print_row("RabbitMQ", ip, RABBITMQ_SERVICE_PORT, ok)
            overall_ok = overall_ok and ok
    else:
        logger.info("Skipping rabbitMQ service test; no service defined.")

    # ---- ELASTICSEARCH -----------------------------------------
    if ES_SERVICE_NAME and ES_PORT:
        for ip in resolve_name(ES_SERVICE_NAME):
            ok = tcp_probe(ip, ES_PORT)
            if ok:
                health_url = f"http://{ip}:{ES_PORT}/_cluster/health"
                ok = check_service_health(health_url)
            _print_row("Elastic", ip, ES_PORT, ok)
            overall_ok = overall_ok and ok
    else:
        logger.info("Skipping elasticsearch service test; no service defined.")

    # ---- POSTGRESQL --------------------------------------------
    if PGHOST and PGPORT:
        for ip in resolve_name(PGHOST):
            ok = tcp_probe(ip, PGPORT)
            if ok:
                ok = check_postgresql_connection(ip, PGPORT, PGUSER, PGDATABASE)
            _print_row("PostgreSQL", ip, PGPORT, ok)
            overall_ok = overall_ok and ok
    else:
        logger.info("Skipping PostgreSQL service test; no service defined.")

    return overall_ok

def status(interval=120):
    """Periodically probe all endpoints and log their status."""
    def probe():
        while True:
            logger.info("=== Periodic Service Status Check ===")

            # MinIO HTTP health check (if env set), else TCP
            if MINIO_SERVICE_ENDPOINT:
                minio_health_url = f"{MINIO_SERVICE_ENDPOINT}/minio/health/live"
                check_service_health(minio_health_url)

            #  Cassandra health check
            cassandra_host = CASSANDRA_SERVICE_NAME
            cassandra_port = CASSANDRA_SERVICE_PORT
            check_cassandra_health(cassandra_host, cassandra_port)

            # RabbitMQ HTTP health check (if env set), else TCP
            rabbitmq_host = RABBITMQ_SERVICE_NAME
            rabbitmq_port = RABBITMQ_SERVICE_PORT
            rabbitmq_mgmt_port = RABBITMQ_MGMT_PORT
            rabbitmq_health_url = f"http://{rabbitmq_host}:{rabbitmq_mgmt_port}/api/overview" if rabbitmq_host and rabbitmq_mgmt_port else None
            rabbitmq_nodes_url = f"http://{rabbitmq_host}:{rabbitmq_mgmt_port}/api/nodes"

            check_rabbitmq_service_health(
                rabbitmq_health_url,
                username=RABBITMQ_USERNAME,
                password=RABBITMQ_PASSWORD
            )
            check_rabbitmq_running_nodes(
                rabbitmq_nodes_url,
                username=RABBITMQ_USERNAME,
                password=RABBITMQ_PASSWORD
            )

            # Elasticsearch HTTP health check (if env set), else TCP
            es_host = ES_SERVICE_NAME
            es_port = ES_PORT
            es_health_url = f"http://{es_host}:{es_port}/_cluster/health" if es_host and es_port else None

            check_service_health(es_health_url)

            check_postgresql_connection(
                host=PGHOST,
                port=PGPORT,
                username=PGUSER,
                database=PGDATABASE
            )
            time.sleep(interval)

    # Run the probe in a background thread so it doesn't block the main loop
    thread = threading.Thread(target=probe, daemon=True)
    thread.start()

def _interactive_shell():
    """Main entrypoint, providing an interactive shell session"""
    logger.info("Starting Wire utility debug pod...")

    # Start periodic status checks
    if ENABLE_PROBE_THREAD:
        status(interval=300)

    # Check services
    minio_status, cassandra_status, rabbitmq_status, es_status, pg_status = check_all_services()

    logger.info("Configuring Client Tools")

    # Configure clients
    configure_minio()
    configure_cassandra()
    configure_rabbitmq()

    # Create utility scripts
    create_status_script()

    current_user = getpass.getuser()

    if current_user == EXPECTED_USER:
        create_bashrc()
    else:
        print(f"Skipping .bashrc creation: User '{current_user}' is not the expected user.")

    logger.info("Startup Complete")
    logger.info("Wire utility debug pod ready!")
    logger.info(f"Use: kubectl exec -it {os.getenv('HOSTNAME', 'pod')} -- bash")

    # Keep container running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)

def _dispatch():
    """CLI dispatcher used when the container is started directly.

    Handles three commands:
      * interactive – runs the full interactive start‑up (formerly `main()`)
      * status      – fast one‑host check (just exits 0)
      * status-full – exhaustive multi‑IP pre‑flight check
    """
    parser = argparse.ArgumentParser(prog="wire-utility")
    parser.add_argument(
        "command",
        nargs="?",
        default="interactive",
        choices=["interactive", "status", "status-full", "versions"],
        help="interactive shell (default), quick status, full multi‑IP status, or versions",
    )
    args = parser.parse_args()

    if args.command == "interactive":
        _interactive_shell()
    elif args.command == "status":
        # Fast path – the Bash alias `status` already prints a table.
        # We simply exit with success so the container can be used as a one‑shot check.
        sys.exit(0)
    elif args.command == "status-full":
        ok = status_full()
        sys.exit(0 if ok else 1)
    elif args.command == "versions":
        # Print a compact JSON map of all discovered versions.
        vers = show_versions()
        import json
        print(json.dumps(vers, indent=2, sort_keys=True))
        sys.exit(0)

if __name__ == "__main__":
    _dispatch()
