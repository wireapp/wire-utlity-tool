#!/usr/bin/env python3
# filepath: scripts/postgres-endpoint-manager.py

import os
import sys
import json
import subprocess
import logging
import argparse
import concurrent.futures
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Kubernetes client imports (with fallback for environments without it)
try:
    from kubernetes import client, config
    KUBERNETES_CLIENT_AVAILABLE = True
except ImportError:
    KUBERNETES_CLIENT_AVAILABLE = False


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

        # Performance optimizations
        self.node_cache = {}
        self.cache_ttl = 30  # Cache node status for 30 seconds
        self.max_workers = int(os.environ.get('MAX_WORKERS', '3'))  # Parallel processing
        self.connection_timeout = int(os.environ.get('PGCONNECT_TIMEOUT', '5'))

        # Kubernetes configuration - handle both K8s and local testing
        self.is_k8s_environment = self.check_k8s_environment()

        if self.is_k8s_environment:
            self.namespace = self.read_file('/var/run/secrets/kubernetes.io/serviceaccount/namespace')
        else:
            self.log_warning("Not running in Kubernetes environment", {
                "kubernetes_environment": False
            })
            self.log_error("PostgreSQL endpoint manager requires Kubernetes environment")
            sys.exit(1)

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

    def get_nodes_from_environment(self) -> List[Tuple[str, str]]:
        """Get PostgreSQL nodes from environment variables"""
        nodes = []

        # Get PostgreSQL node IPs from PG_NODES environment variable
        pg_nodes = os.environ.get('PG_NODES', '')
        if pg_nodes:
            for ip in pg_nodes.split(','):
                ip = ip.strip()
                if ip:
                    # Create node name from IP by replacing dots with dashes
                    node_name = f"pg-{ip.replace('.', '-')}"
                    nodes.append((ip, node_name))

        if not nodes:
            self.log_error("No PostgreSQL nodes found in PG_NODES environment variable")
            sys.exit(1)

        self.log_info("Nodes discovered from environment variables", {
            "pg_nodes": pg_nodes,
            "total_nodes": len(nodes),
            "nodes": [{"ip": ip, "name": name} for ip, name in nodes]
        })

        return nodes

    def get_stored_topology(self) -> Optional[str]:
        """Get stored topology from RW service annotations"""
        if not self.is_k8s_environment:
            return None

        self.log_info("Fetching stored topology from RW service annotations", {
            "service": self.rw_service,
            "method": "kubernetes_client"
        })

        if not KUBERNETES_CLIENT_AVAILABLE:
            self.log_error("Kubernetes client not available for reading topology", {
                "service": self.rw_service
            })
            return None

        try:
            # Load in-cluster config
            config.load_incluster_config()

            # Create API client
            v1 = client.CoreV1Api()

            # Get the endpoint
            endpoint = v1.read_namespaced_endpoints(
                name=self.rw_service,
                namespace=self.namespace
            )

            # Extract annotations
            annotations = endpoint.metadata.annotations or {}
            stored_topology = annotations.get('postgres.discovery/last-topology', '')

            self.log_info("Retrieved stored topology", {
                "stored_topology": stored_topology if stored_topology else "none",
                "has_annotations": bool(annotations),
                "service": self.rw_service,
                "method": "kubernetes_client"
            })

            return stored_topology if stored_topology else None

        except client.exceptions.ApiException as e:
            self.log_warning("Failed to get stored topology", {
                "error": str(e),
                "service": self.rw_service,
                "status_code": e.status if hasattr(e, 'status') else 'unknown',
                "method": "kubernetes_client"
            })
            return None
        except Exception as e:
            self.log_error("Unexpected error reading stored topology", {
                "error": str(e),
                "error_type": type(e).__name__,
                "service": self.rw_service,
                "method": "kubernetes_client"
            })
            return None

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
            self.log_info("Executing command", {
                "command": command,
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
                "command": command,
                "return_code": result.returncode,
                "success": success,
                "stdout_length": len(result.stdout),
                "stderr_length": len(result.stderr)
            })

            return success, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.log_warning("Command timeout", {
                "command": command,
                "timeout": timeout
            })
            return False, "", "Command timeout"
        except Exception as e:
            self.log_error("Command execution failed", {
                "command": command,
                "error": str(e),
                "error_type": type(e).__name__
            })
            return False, "", str(e)

    def check_postgres_node_with_cache(self, ip: str, name: str) -> Optional[str]:
        """Check PostgreSQL node status with caching for performance"""
        cache_key = f"{ip}:{name}"
        current_time = time.time()

        # Check cache first
        if cache_key in self.node_cache:
            cached_data = self.node_cache[cache_key]
            if current_time - cached_data['timestamp'] < self.cache_ttl:
                self.log_info("Using cached node status", {
                    "node_name": name,
                    "node_ip": ip,
                    "status": cached_data['status'],
                    "cache_age": current_time - cached_data['timestamp']
                })
                return cached_data['status']

        # Cache miss or expired, check node
        status = self.check_postgres_node(ip, name)

        # Cache the result
        self.node_cache[cache_key] = {
            'status': status,
            'timestamp': current_time
        }

        return status

    def check_postgres_node(self, ip: str, name: str) -> Optional[str]:
        """Check if PostgreSQL node is primary or standby"""
        self.log_info("Checking PostgreSQL node", {
            "node_name": name,
            "node_ip": ip
        })

        # Test connectivity first with optimized timeout
        connectivity_cmd = f"pg_isready -h {ip} -p 5432 -t {self.connection_timeout}"
        success, _, _ = self.run_command(connectivity_cmd, timeout=self.connection_timeout)
        if not success:
            self.log_info("Node status determined", {
                "node_name": name,
                "node_ip": ip,
                "status": "DOWN",
                "connectivity_check": "failed",
                "timeout": self.connection_timeout
            })
            return None

        # Check if primary or standby with optimized query
        recovery_cmd = f"psql -h {ip} -t -A -c \"SELECT pg_is_in_recovery();\" --set ON_ERROR_STOP=1"
        success, stdout, stderr = self.run_command(recovery_cmd, timeout=self.connection_timeout)

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
        """Verify the actual topology of discovered nodes with parallel processing"""
        self.log_info("Starting topology verification", {
            "total_nodes": len(nodes),
            "max_workers": self.max_workers,
            "parallel_processing": True
        })

        primary_ip = None
        primary_name = None
        standby_ips = []

        # Use ThreadPoolExecutor for parallel node checking
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all node checks
            future_to_node = {
                executor.submit(self.check_postgres_node_with_cache, ip, name): (ip, name)
                for ip, name in nodes
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_node):
                ip, name = future_to_node[future]
                try:
                    status = future.result(timeout=self.connection_timeout + 5)

                    if status == 'primary':
                        primary_ip = ip
                        primary_name = name
                    elif status == 'standby':
                        standby_ips.append(ip)

                except Exception as e:
                    self.log_warning("Node check failed", {
                        "node_name": name,
                        "node_ip": ip,
                        "error": str(e),
                        "error_type": type(e).__name__
                    })

        topology = {
            'primary_ip': primary_ip,
            'primary_name': primary_name,
            'standby_ips': sorted(standby_ips)  # Sort for consistency
        }

        self.log_info("Topology verification completed", {
            "primary_ip": primary_ip,
            "primary_name": primary_name,
            "standby_ips": topology['standby_ips'],
            "standby_count": len(topology['standby_ips']),
            "total_healthy_nodes": (1 if primary_ip else 0) + len(topology['standby_ips']),
            "parallel_processing": True
        })

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
        """Update Kubernetes endpoint using Python Kubernetes client"""
        self.log_info("Updating endpoint with Kubernetes client", {
            "service_name": service_name,
            "description": description,
            "target_ips": target_ips,
            "method": "kubernetes_client"
        })

        if not KUBERNETES_CLIENT_AVAILABLE:
            self.log_error("Kubernetes client not available for endpoint update", {
                "service_name": service_name
            })
            return False

        try:
            # Load in-cluster config
            config.load_incluster_config()

            # Create API client
            v1 = client.CoreV1Api()

            # Create endpoint addresses
            addresses = [client.V1EndpointAddress(ip=ip) for ip in target_ips] if target_ips else []

            # Create endpoint subset
            subset = client.V1EndpointSubset(
                addresses=addresses,
                ports=[client.V1EndpointPort(port=5432, protocol="TCP", name="postgresql")]
            )

            # Create endpoint object with annotations
            endpoint = client.V1Endpoints(
                metadata=client.V1ObjectMeta(
                    name=service_name,
                    annotations={
                        "postgres.discovery/last-topology": topology_signature,
                        "postgres.discovery/last-update": datetime.now(timezone.utc).isoformat()
                    }
                ),
                subsets=[subset]
            )

            # Patch the endpoint (merge patch to update only specified fields)
            v1.patch_namespaced_endpoints(
                name=service_name,
                namespace=self.namespace,
                body=endpoint
            )

            self.log_info("Endpoint updated successfully", {
                "service_name": service_name,
                "description": description,
                "target_ips": target_ips,
                "method": "kubernetes_client",
                "annotations_updated": True
            })
            return True

        except client.exceptions.ApiException as e:
            self.log_error("Kubernetes client endpoint update failed", {
                "service_name": service_name,
                "error": str(e),
                "status_code": e.status if hasattr(e, 'status') else 'unknown',
                "method": "kubernetes_client"
            })
            return False
        except Exception as e:
            self.log_error("Unexpected error updating endpoint", {
                "service_name": service_name,
                "error": str(e),
                "error_type": type(e).__name__,
                "method": "kubernetes_client"
            })
            return False

    def run(self):
        """Main execution method with performance monitoring"""
        start_time = time.time()

        try:
            self.log_info("Starting PostgreSQL endpoint manager run", {
                "rw_service": self.rw_service,
                "ro_service": self.ro_service,
                "namespace": self.namespace,
                "environment": "kubernetes",
                "max_workers": self.max_workers,
                "cache_ttl": self.cache_ttl
            })

            # Get all configured nodes from environment variables
            all_nodes = self.get_nodes_from_environment()
            if not all_nodes:
                self.log_error("No nodes found in environment configuration")
                return False

            # Get stored topology to compare changes
            stored_topology = self.get_stored_topology()

            # Verify live status of all configured nodes
            verified_topology = self.verify_topology(all_nodes)
            if not verified_topology['primary_ip']:
                self.log_error("No primary node found among configured nodes", {
                    "checked_nodes": len(all_nodes),
                    "standby_count": len(verified_topology['standby_ips']),
                    "configured_nodes": [{"ip": ip, "name": name} for ip, name in all_nodes]
                })
                return False

            # Create current topology string
            current_topology = self.create_topology_signature(verified_topology)

            # Check if topology has changed
            if stored_topology and stored_topology == current_topology:
                self.log_info("Topology unchanged, skipping endpoint updates", {
                    "current_topology": current_topology
                })
                return True

            self.log_info("Topology changed, updating endpoints", {
                "stored_topology": stored_topology or "none",
                "current_topology": current_topology,
                "changes_detected": True
            })

            # Update services with current topology in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # Submit both endpoint updates
                rw_future = executor.submit(
                    self.update_endpoint,
                    self.rw_service,
                    [verified_topology['primary_ip']],
                    "Read-Write",
                    current_topology
                )

                ro_future = executor.submit(
                    self.update_endpoint,
                    self.ro_service,
                    verified_topology['standby_ips'],
                    "Read-Only",
                    current_topology
                )

                # Wait for completion and collect results
                rw_result = rw_future.result(timeout=30)
                ro_result = ro_future.result(timeout=30)

            updates = (1 if rw_result else 0) + (1 if ro_result else 0)

            if updates == 2:
                execution_time = time.time() - start_time
                self.log_info("PostgreSQL endpoint manager run completed successfully", {
                    "primary_updated": bool(verified_topology['primary_ip']),
                    "standbys_updated": len(verified_topology['standby_ips']),
                    "topology_stored": True,
                    "current_topology": current_topology,
                    "execution_time_seconds": round(execution_time, 2),
                    "performance_optimized": True
                })
                return True
            else:
                execution_time = time.time() - start_time
                self.log_error("Failed to update one or more services", {
                    "updates_successful": updates,
                    "updates_expected": 2,
                    "execution_time_seconds": round(execution_time, 2)
                })
                return False

        except Exception as e:
            execution_time = time.time() - start_time
            self.log_error("Unexpected error in main run", {
                "error": str(e),
                "execution_time_seconds": round(execution_time, 2)
            })
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='PostgreSQL Endpoint Manager - Environment Variable Discovery',
        epilog="""
How it works:
  1. Discovers nodes from PG_NODES environment variable
  2. Verifies actual status of all configured PostgreSQL nodes
  3. Updates endpoints only if topology has changed

Environment Variables:
  PG_NODES             PostgreSQL node IPs (comma-separated)
  RW_SERVICE           Read-Write service name (default: postgresql-external-rw)
  RO_SERVICE           Read-Only service name (default: postgresql-external-ro)
  PGPASSWORD           PostgreSQL password (default: securepassword)
  PGUSER               PostgreSQL username (default: repmgr)
  PGDATABASE           PostgreSQL database (default: repmgr)
  PGCONNECT_TIMEOUT    Connection timeout in seconds (default: 5)
  MAX_WORKERS          Max parallel workers for node checking (default: 3)

Performance Features:
  - Parallel node checking for faster topology discovery
  - Node status caching to reduce redundant checks
  - Optimized connection timeouts and error handling
  - Parallel endpoint updates for faster completion

Discovery Method:
  - Discovers ALL nodes from PG_NODES environment variable
  - Verifies each node's current role (primary/standby/down)
  - Always checks ALL configured nodes (fixes shrinking cluster issue)
  - Updates endpoints only when topology changes

Examples:
  # Normal operation with environment variables
  PG_NODES="192.168.122.31,192.168.122.32,192.168.122.33" \\
  python3 postgres-endpoint-manager.py
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--version', action='version', version='1.0.0')
    args = parser.parse_args()

    try:
        manager = PostgreSQLEndpointManager()
        success = manager.run()
        sys.exit(0 if success else 1)
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