import os
import sys
import threading
import time
import socket
import subprocess
import logging
from pathlib import Path
from cassandra.cluster import Cluster, ExecutionProfile, EXEC_PROFILE_DEFAULT
from cassandra.policies import RoundRobinPolicy

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

CASSANDRA_SERVICE_NAME = os.getenv('CASSANDRA_SERVICE_NAME', 'cassandra')
CASSANDRA_SERVICE_PORT = int(os.getenv('CASSANDRA_SERVICE_PORT', '9042'))

RABBITMQ_SERVICE_NAME = os.getenv('RABBITMQ_SERVICE_NAME', 'rabbitmq')
RABBITMQ_SERVICE_PORT = int(os.getenv('RABBITMQ_SERVICE_PORT', '5672'))
RABBITMQ_MGMT_PORT = os.getenv('RABBITMQ_MGMT_PORT', '15672')
RABBITMQ_USERNAME = os.getenv('RABBITMQ_USERNAME', 'guest')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_PASSWORD', 'guest')

ES_SERVICE_NAME = os.getenv('ES_SERVICE_NAME', 'elasticsearch-external')
ES_PORT = os.getenv('ES_PORT', '9200')


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
    """Check if a service is reachable"""
    try:
        with socket.create_connection((host, port), timeout=3):
            logger.info(f"{name} ({host}:{port}) is reachable")
            return True
    except (socket.error, socket.timeout):
        logger.error(f"{name} ({host}:{port}) is unreachable")
        try:
            ip = socket.gethostbyname(host)
            logger.info(f"Resolved {name} to IP: {ip}")
        except Exception as e:
            logger.error(f"Failed to resolve {name} host: {e}")
        return False

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

echo ""
echo "=== Quick Commands ==="
echo "status                    # Show this status"
echo "mc ls wire-minio          # List MinIO buckets"
echo "cqlsh                     # Connect to Cassandra"
echo "rabbitmqadmin list queues # List RabbitMQ queues"
echo "es-debug.py usages        # List available commands to run with es-debug.py (e.g es-debug.py health) to debug Elasticsearch"
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
    bashrc_file.write_text(bashrc_content)

def check_all_services():
    """Check connectivity to all services"""
    logger.info("Checking Service Connectivity")

    # MinIO
    minio_endpoint = MINIO_SERVICE_ENDPOINT
    minio_host, minio_port = parse_minio_endpoint(minio_endpoint)
    minio_status = False
    if minio_host and minio_port:
        minio_status = check_service(minio_host, minio_port, "MinIO")

    # Cassandra
    cassandra_host = CASSANDRA_SERVICE_NAME
    cassandra_port = CASSANDRA_SERVICE_PORT
    cassandra_status = check_service(cassandra_host, cassandra_port, "Cassandra")

    # RabbitMQ
    rabbitmq_host = RABBITMQ_SERVICE_NAME
    rabbitmq_port = int(RABBITMQ_SERVICE_PORT)
    rabbitmq_status = check_service(rabbitmq_host, rabbitmq_port, "RabbitMQ")

    # Elasticsearch
    es_host = ES_SERVICE_NAME
    es_port = int(ES_PORT)
    es_status = check_service(es_host, es_port, "Elasticsearch")

    return minio_status, cassandra_status, rabbitmq_status, es_status

def check_cassandra_health(host, port, username=None, password=None):
    """Check Cassandra health using cassandra-driver with execution profiles."""
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

def status(interval=30):
    """Periodically probe all endpoints and log their status."""
    def probe():
        while True:
            logger.info("=== Periodic Service Status Check ===")

            # MinIO HTTP health check (if env set), else TCP
            minio_endpoint = MINIO_SERVICE_ENDPOINT
            minio_health_url = f"{minio_endpoint}/minio/health/live" if minio_endpoint else None
            check_service_health(minio_health_url)            

            #  Cassandra health check
            cassandra_host = CASSANDRA_SERVICE_NAME
            cassandra_port = int(CASSANDRA_SERVICE_PORT)
            check_cassandra_health(cassandra_host, cassandra_port)

            # RabbitMQ HTTP health check (if env set), else TCP
            rabbitmq_host = RABBITMQ_SERVICE_NAME
            rabbitmq_port = int(RABBITMQ_SERVICE_PORT)
            rabbitmq_mgmt_port = int(RABBITMQ_MGMT_PORT)
            rabbitmq_health_url = f"http://{rabbitmq_host}:{rabbitmq_mgmt_port}/api/overview" if rabbitmq_host and rabbitmq_mgmt_port else None
            if rabbitmq_health_url:
                check_service_health(rabbitmq_health_url)
            else:
                check_service(rabbitmq_host, rabbitmq_port, "RabbitMQ")

            # Elasticsearch HTTP health check (if env set), else TCP
            es_host = ES_SERVICE_NAME
            es_port = int(ES_PORT)
            es_health_url = f"http://{es_host}:{es_port}/_cluster/health" if es_host and es_port else None
            if es_health_url:
                check_service_health(es_health_url)
            else:
                check_service(es_host, int(es_port), "Elasticsearch")

            time.sleep(interval)

    # Run the probe in a background thread so it doesn't block the main loop
    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    
def main():
    """Main entrypoint function"""
    logger.info("Starting Wire utility debug pod...")

    # Start periodic status checks
    status(interval=30)

    # Check services
    minio_status, cassandra_status, rabbitmq_status, es_status = check_all_services()

    logger.info("Configuring Client Tools")

    # Configure clients
    configure_minio()
    configure_cassandra()
    configure_rabbitmq()

    # Create utility scripts
    create_status_script()
    create_bashrc()

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

if __name__ == "__main__":
    main()