#!/usr/bin/env python3
# filepath: scripts/postgres-endpoint-manager.py

import os
import sys
import json
import subprocess
import logging
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured JSON logging"""

    def format(self, record):
        # Create base log structure
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "component": "postgres-endpoint-manager"
        }

        # Add extra fields if present
        if hasattr(record, 'extra_fields') and record.extra_fields:
            log_entry.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


# Configure structured logging
def setup_logging():
    """Setup structured JSON logging"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create console handler with structured formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)

    # Prevent duplicate logs
    logger.propagate = False

    return logger


# Initialize structured logger
logger = setup_logging()

class PostgreSQLEndpointManager:
    def __init__(self):
        # Environment setup
        self.setup_environment()

        # Kubernetes configuration - handle both K8s and local testing
        self.is_k8s_environment = self.check_k8s_environment()

        if self.is_k8s_environment:
            self.token = self.read_file('/var/run/secrets/kubernetes.io/serviceaccount/token')
            self.namespace = self.read_file('/var/run/secrets/kubernetes.io/serviceaccount/namespace')
            self.api_server = "https://kubernetes.default.svc.cluster.local"
        else:
            self.log_warning("Not running in Kubernetes environment - using test mode", {
                "kubernetes_environment": False
            })
            self.token = "test-token"
            self.namespace = "test-namespace"
            self.api_server = "test-api-server"

        # Service configuration
        self.rw_service = os.environ.get('RW_SERVICE', 'postgresql-external-rw')
        self.ro_service = os.environ.get('RO_SERVICE', 'postgresql-external-ro')

        self.log_info("PostgreSQL Endpoint Manager initialized", {
            "namespace": self.namespace,
            "kubernetes_environment": self.is_k8s_environment,
            "rw_service": self.rw_service,
            "ro_service": self.ro_service
        })

    def log_info(self, message: str, extra_fields: Dict = None):
        """Log info message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.INFO, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        record.extra_fields = extra_fields or {}
        logger.handle(record)

    def log_warning(self, message: str, extra_fields: Dict = None):
        """Log warning message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.WARNING, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        record.extra_fields = extra_fields or {}
        logger.handle(record)

    def log_error(self, message: str, extra_fields: Dict = None):
        """Log error message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.ERROR, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        record.extra_fields = extra_fields or {}
        logger.handle(record)

    def check_k8s_environment(self) -> bool:
        """Check if running in Kubernetes environment"""
        return os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token')

    def parse_topology_string(self, topology_string: str) -> List[Tuple[str, str]]:
        """Parse topology string into node list"""
        if not topology_string:
            return []

        # Parse topology: "primary:192.168.122.31;standbys:192.168.122.32,192.168.122.33"
        nodes = []
        parts = topology_string.split(';')

        for part in parts:
            if part.startswith('primary:'):
                primary_ip = part.split(':', 1)[1]
                if primary_ip:
                    nodes.append((primary_ip, f"primary-{primary_ip.replace('.', '-')}"))
            elif part.startswith('standbys:'):
                standby_ips = part.split(':', 1)[1]
                if standby_ips:
                    for ip in standby_ips.split(','):
                        ip = ip.strip()
                        if ip:
                            nodes.append((ip, f"standby-{ip.replace('.', '-')}"))

        return nodes

    def get_endpoint_annotations_and_nodes(self) -> Tuple[List[Tuple[str, str]], Optional[str]]:
        """Get both node list and stored topology in single API call"""
        if not self.is_k8s_environment:
            self.log_info("Using test nodes in non-K8s environment")
            test_nodes = [
                ("192.168.122.31", "ansnode1"),
                ("192.168.122.32", "ansnode2"),
                ("192.168.122.33", "ansnode3")
            ]
            return test_nodes, None

        self.log_info("Fetching RW service endpoint annotations")

        cmd = (
            f"curl -s -k "
            f"-H 'Authorization: Bearer {self.token}' "
            f"'{self.api_server}/api/v1/namespaces/{self.namespace}/endpoints/{self.rw_service}'"
        )

        success, stdout, stderr = self.run_command(cmd, timeout=15)

        if not success:
            self.log_warning("Failed to get RW service endpoint", {
                "error": stderr,
                "service": self.rw_service,
                "namespace": self.namespace
            })
            return [], None

        try:
            endpoint_data = json.loads(stdout)
            annotations = endpoint_data.get('metadata', {}).get('annotations', {})

            # Get stored topology from annotations
            stored_topology = annotations.get('postgres.discovery/last-topology', '')

            if not stored_topology:
                self.log_info("No stored topology found in annotations - likely first run")
                return [], None

            # Parse nodes from topology string
            nodes = self.parse_topology_string(stored_topology)

            self.log_info("Retrieved endpoint data successfully", {
                "source": "rw_service_annotations",
                "stored_topology": stored_topology,
                "nodes_count": len(nodes),
                "nodes": [{"ip": ip, "name": name} for ip, name in nodes],
                "has_annotations": bool(annotations)
            })

            return nodes, stored_topology

        except json.JSONDecodeError as e:
            self.log_error("Failed to parse RW service endpoint response", {
                "error": str(e),
                "service": self.rw_service,
                "response_length": len(stdout)
            })
            return [], None

    def setup_environment(self):
        """Setup PostgreSQL environment variables"""
        os.environ['PGPASSWORD'] = os.environ.get('PGPASSWORD', 'securepassword')
        os.environ['PGUSER'] = os.environ.get('PGUSER', 'repmgr')
        os.environ['PGDATABASE'] = os.environ.get('PGDATABASE', 'repmgr')
        os.environ['PGCONNECT_TIMEOUT'] = os.environ.get('PGCONNECT_TIMEOUT', '5')
        os.environ['TZ'] = 'UTC'

    def read_file(self, filepath: str) -> str:
        """Read file content safely"""
        try:
            with open(filepath, 'r') as f:
                content = f.read().strip()

                # Only log successful read for non-sensitive files
                if 'token' in filepath:
                    self.log_info("Successfully read service account token")
                else:
                    self.log_info("Successfully read file", {
                        "filepath": filepath,
                        "content_length": len(content)
                    })
                return content
        except Exception as e:
            self.log_error("Failed to read file", {
                "filepath": filepath,
                "error": str(e),
                "error_type": type(e).__name__
            })
            sys.exit(1)

    def run_command(self, command: str, timeout: int = 5) -> Tuple[bool, str, str]:
        """Run shell command with timeout"""
        try:
            # Mask sensitive information in logs
            log_command = command
            if 'Authorization: Bearer' in command:
                log_command = command.replace(self.token, '[REDACTED]') if hasattr(self, 'token') else '[REDACTED CURL COMMAND]'

            self.log_info("Executing command", {
                "command": log_command,
                "timeout": timeout
            })

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            success = result.returncode == 0
            self.log_info("Command completed", {
                "command": log_command,
                "return_code": result.returncode,
                "success": success,
                "stdout_length": len(result.stdout),
                "stderr_length": len(result.stderr)
            })

            return success, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.log_warning("Command timeout", {
                "command": log_command if 'log_command' in locals() else "[COMMAND]",
                "timeout": timeout
            })
            return False, "", "Command timeout"
        except Exception as e:
            self.log_error("Command execution failed", {
                "command": log_command if 'log_command' in locals() else "[COMMAND]",
                "error": str(e),
                "error_type": type(e).__name__
            })
            return False, "", str(e)

    def check_postgres_node(self, ip: str, name: str) -> Optional[str]:
        """Check if PostgreSQL node is primary or standby"""
        self.log_info("Checking PostgreSQL node", {
            "node_name": name,
            "node_ip": ip,
            "test_mode": not self.is_k8s_environment
        })

        if not self.is_k8s_environment:
            # In test mode, simulate some nodes for demonstration
            if ip == "192.168.122.31":
                self.log_info("Node status determined", {
                    "node_name": name,
                    "node_ip": ip,
                    "status": "PRIMARY",
                    "simulated": True
                })
                return 'primary'
            elif ip == "192.168.122.32":
                self.log_info("Node status determined", {
                    "node_name": name,
                    "node_ip": ip,
                    "status": "STANDBY",
                    "simulated": True
                })
                return 'standby'
            else:
                self.log_info("Node status determined", {
                    "node_name": name,
                    "node_ip": ip,
                    "status": "DOWN",
                    "simulated": True
                })
                return None

        # Test connectivity first
        success, _, _ = self.run_command(f"pg_isready -h {ip} -p 5432")
        if not success:
            self.log_info("Node status determined", {
                "node_name": name,
                "node_ip": ip,
                "status": "DOWN",
                "connectivity_check": "failed"
            })
            return None

        # Check if primary or standby
        success, stdout, stderr = self.run_command(
            f"psql -h {ip} -t -A -c \"SELECT pg_is_in_recovery();\""
        )

        if not success:
            self.log_warning("Node status unknown", {
                "node_name": name,
                "node_ip": ip,
                "error": stderr,
                "recovery_check": "failed"
            })
            return None

        recovery_status = stdout.strip()

        if recovery_status == 'f':
            self.log_info("Node status determined", {
                "node_name": name,
                "node_ip": ip,
                "status": "PRIMARY",
                "recovery_status": recovery_status
            })
            return 'primary'
        elif recovery_status == 't':
            self.log_info("Node status determined", {
                "node_name": name,
                "node_ip": ip,
                "status": "STANDBY",
                "recovery_status": recovery_status
            })
            return 'standby'
        else:
            self.log_warning("Node status unknown", {
                "node_name": name,
                "node_ip": ip,
                "status": "UNKNOWN",
                "recovery_status": recovery_status
            })
            return None

    def verify_topology(self, nodes: List[Tuple[str, str]]) -> Dict:
        """Verify the actual topology of discovered nodes"""
        self.log_info("Starting topology verification", {
            "total_nodes": len(nodes)
        })

        primary_ip = None
        primary_name = None
        standby_ips = []

        for ip, name in nodes:
            status = self.check_postgres_node(ip, name)

            if status == 'primary':
                primary_ip = ip
                primary_name = name
            elif status == 'standby':
                standby_ips.append(ip)

        topology = {
            'primary_ip': primary_ip,
            'primary_name': primary_name,
            'standby_ips': standby_ips
        }

        self.log_info("Topology verification completed", {
            "primary_ip": primary_ip,
            "primary_name": primary_name,
            "standby_ips": standby_ips,
            "standby_count": len(standby_ips),
            "total_healthy_nodes": (1 if primary_ip else 0) + len(standby_ips)
        })

        return topology

    def discover_topology(self) -> Dict:
        """Discover PostgreSQL cluster topology"""
        self.log_info("Starting PostgreSQL topology discovery", {
            "total_nodes": len(self.nodes)
        })

        primary_ip = None
        primary_name = None
        standby_ips = []

        for ip, name in self.nodes:
            status = self.check_postgres_node(ip, name)

            if status == 'primary':
                primary_ip = ip
                primary_name = name
            elif status == 'standby':
                standby_ips.append(ip)

        topology = {
            'primary_ip': primary_ip,
            'primary_name': primary_name,
            'standby_ips': standby_ips
        }

        self.log_info("Topology discovery completed", {
            "primary_ip": primary_ip,
            "primary_name": primary_name,
            "standby_ips": standby_ips,
            "standby_count": len(standby_ips),
            "total_healthy_nodes": (1 if primary_ip else 0) + len(standby_ips)
        })

        if not primary_ip:
            self.log_error("No primary PostgreSQL node found", {
                "checked_nodes": len(self.nodes),
                "standby_count": len(standby_ips)
            })
            sys.exit(1)

        return topology

    def create_topology_signature(self, topology: Dict) -> str:
        """Create topology signature for change detection"""
        primary_ip = topology['primary_ip']
        standby_ips = sorted(topology['standby_ips'])
        standby_list = ','.join(standby_ips) if standby_ips else ''

        signature = f"primary:{primary_ip};standbys:{standby_list}"

        self.log_info("Topology signature created", {
            "signature": signature,
            "primary_ip": primary_ip,
            "standby_ips": standby_ips
        })

        return signature

    def update_endpoint(self, service_name: str, target_ips: List[str], description: str, topology_signature: str) -> bool:
        """Update Kubernetes endpoint"""
        if not self.is_k8s_environment:
            self.log_info("Simulating endpoint update in test mode", {
                "service_name": service_name,
                "description": description,
                "target_ips": target_ips,
                "ip_count": len(target_ips) if target_ips else 0
            })
            return True

        self.log_info("Starting endpoint update", {
            "service_name": service_name,
            "description": description,
            "target_ips": target_ips,
            "ip_count": len(target_ips) if target_ips else 0,
            "topology_signature": topology_signature
        })

        # Build addresses array
        addresses = [{"ip": ip} for ip in target_ips] if target_ips else []

        # Create update payload
        update_payload = {
            "metadata": {
                "annotations": {
                    "postgres.discovery/last-topology": topology_signature,
                    "postgres.discovery/last-update": datetime.now(timezone.utc).isoformat()
                }
            },
            "subsets": [{
                "addresses": addresses,
                "ports": [{"port": 5432, "protocol": "TCP", "name": "postgresql"}]
            }]
        }

        # Convert to JSON
        payload_json = json.dumps(update_payload)

        # Update endpoint
        cmd = (
            f"curl -s -k "
            f"-X PATCH "
            f"-H 'Authorization: Bearer {self.token}' "
            f"-H 'Content-Type: application/merge-patch+json' "
            f"-d '{payload_json}' "
            f"'{self.api_server}/api/v1/namespaces/{self.namespace}/endpoints/{service_name}'"
        )

        success, stdout, stderr = self.run_command(cmd, timeout=20)

        if success:
            self.log_info("Endpoint update successful", {
                "service_name": service_name,
                "description": description,
                "target_ips": target_ips,
                "topology_signature": topology_signature
            })
            return True
        else:
            self.log_error("Endpoint update failed", {
                "service_name": service_name,
                "description": description,
                "error": stderr,
                "target_ips": target_ips
            })
            return False

    def run(self):
        """Main execution logic with optimized single API call"""
        start_time = datetime.now(timezone.utc)

        self.log_info("PostgreSQL Endpoint Manager started", {
            "start_time": start_time.isoformat(),
            "version": "1.0.0"
        })

        try:
            # Step 1: Get both nodes and stored topology in single API call
            self.log_info("Fetching endpoint annotations and discovering nodes")
            nodes, stored_topology = self.get_endpoint_annotations_and_nodes()

            if not nodes:
                self.log_warning("No nodes discovered from annotations", {
                    "reason": "first_run_or_no_annotations",
                    "stored_topology": stored_topology,
                    "action": "requires_bootstrap"
                })
                # For first run, we need some way to bootstrap
                return 1

            self.log_info("Nodes discovered - proceeding with topology verification", {
                "nodes_count": len(nodes),
                "nodes": [{"ip": ip, "name": name} for ip, name in nodes],
                "stored_topology": stored_topology
            })

            # Step 2: Verify current topology by checking each discovered node
            self.log_info("Verifying actual topology of discovered nodes")
            verified_topology = self.verify_topology(nodes)

            if not verified_topology['primary_ip']:
                self.log_error("No primary node found among discovered nodes", {
                    "checked_nodes": len(nodes),
                    "standby_count": len(verified_topology['standby_ips']),
                    "discovered_nodes": [{"ip": ip, "name": name} for ip, name in nodes]
                })
                return 1

            # Step 3: Create topology signature from verified state
            current_topology = self.create_topology_signature(verified_topology)

            # Step 4: Compare current vs stored topology (no additional API call needed!)
            self.log_info("Comparing topologies for changes", {
                "current_topology": current_topology,
                "stored_topology": stored_topology
            })

            if current_topology == stored_topology:
                self.log_info("No topology changes detected", {
                    "current_topology": current_topology,
                    "stored_topology": stored_topology,
                    "action": "none_required",
                    "verified_primary": verified_topology['primary_ip'],
                    "verified_standbys": verified_topology['standby_ips']
                })
                return 0

            self.log_info("Topology change detected - updating endpoints", {
                "current_topology": current_topology,
                "stored_topology": stored_topology,
                "action": "update_endpoints",
                "changes": {
                    "primary_ip": verified_topology['primary_ip'],
                    "standby_ips": verified_topology['standby_ips'],
                    "standby_count": len(verified_topology['standby_ips'])
                }
            })

            # Step 5: Update endpoints with verified topology
            updates = 0

            # Update RW service (primary)
            if self.update_endpoint(
                self.rw_service,
                [verified_topology['primary_ip']],
                "Read-Write",
                current_topology
            ):
                updates += 1

            # Update RO service (standbys)
            if self.update_endpoint(
                self.ro_service,
                verified_topology['standby_ips'],
                "Read-Only",
                current_topology
            ):
                updates += 1

            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()

            self.log_info("PostgreSQL Endpoint Manager completed", {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": duration,
                "updates_successful": updates,
                "updates_expected": 2,
                "primary_ip": verified_topology['primary_ip'],
                "primary_name": verified_topology['primary_name'],
                "standby_ips": verified_topology['standby_ips'],
                "current_topology": current_topology,
                "previous_topology": stored_topology,
                "success": updates == 2
            })

            return 0 if updates == 2 else 1

        except Exception as e:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()

            self.log_error("PostgreSQL Endpoint Manager failed", {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": duration,
                "error": str(e),
                "error_type": type(e).__name__
            })
            return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='PostgreSQL Endpoint Manager - Annotation-Based Discovery',
        epilog="""
How it works:
  1. Reads existing topology from postgresql-external-rw service annotations
  2. Verifies actual status of discovered PostgreSQL nodes
  3. Updates endpoints only if topology has changed

Environment Variables:
  RW_SERVICE           Read-Write service name (default: postgresql-external-rw)
  RO_SERVICE           Read-Only service name (default: postgresql-external-ro)
  PGPASSWORD           PostgreSQL password (default: securepassword)
  PGUSER               PostgreSQL username (default: repmgr)
  PGDATABASE           PostgreSQL database (default: repmgr)
  PGCONNECT_TIMEOUT    Connection timeout in seconds (default: 5)

Discovery Method:
  - Discovers nodes from RW service annotations (postgres.discovery/last-topology)
  - Verifies each node's current role (primary/standby)
  - Updates endpoints only when topology changes

Examples:
  # Normal operation (discovers from annotations)
  python3 postgres-endpoint-manager.py

  # Test mode (simulates K8s environment)
  python3 postgres-endpoint-manager.py --test
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--test', action='store_true', help='Run in test mode (simulate K8s environment)')
    parser.add_argument('--version', action='version', version='1.0.0')
    args = parser.parse_args()

    if args.test:
        logger.info("Running in test mode")

    try:
        manager = PostgreSQLEndpointManager()
        exit_code = manager.run()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down gracefully")
        sys.exit(130)
    except Exception as e:
        record = logging.LogRecord(
            name=logger.name, level=logging.ERROR, pathname="", lineno=0,
            msg="Fatal error during startup", args=(), exc_info=None
        )
        record.extra_fields = {
            "error": str(e),
            "error_type": type(e).__name__,
            "phase": "startup"
        }
        logger.handle(record)
        sys.exit(1)