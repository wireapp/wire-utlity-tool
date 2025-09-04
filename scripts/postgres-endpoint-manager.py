#!/usr/bin/env python3
# filepath: scripts/postgres-endpoint-manager.py

import os
import sys
import json
# subprocess is no longer required since we use psycopg for DB checks
import logging
import shutil
import argparse
import runpy
import concurrent.futures
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any, Union
import socket

# Try to import psycopg (v3). We expect it to be present in the runtime image.
try:
    import psycopg
    PSYCOPG_AVAILABLE = True
except Exception:
    PSYCOPG_AVAILABLE = False

# Kubernetes client imports (with fallback for environments without it)
try:
    from kubernetes import client, config # type: ignore
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
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, 'extra_fields') and record.extra_fields:
            log_entry.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


# Custom exception for control flow inside the library
class EndpointManagerError(Exception):
    pass


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


def retry_with_backoff(fn):
    """Simple retry decorator with exponential backoff and jitter for transient DB errors."""
    import random
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        attempts = kwargs.pop('_attempts', 3)
        delay = kwargs.pop('_delay', 0.2)
        for i in range(attempts):
            try:
                return fn(*args, **kwargs)
            except Exception:
                if i == attempts - 1:
                    raise
                sleep_time = delay * (2 ** i) + random.random() * 0.1
                time.sleep(sleep_time)

    return wrapped

class PostgreSQLEndpointManager:
    def __init__(self):
        # Environment setup
        self.setup_environment()
        self.max_workers = int(os.environ.get('MAX_WORKERS', '3'))  # Parallel processing
        self.connection_timeout = int(os.environ.get('PGCONNECT_TIMEOUT', '5'))

        # Kubernetes configuration - handle both K8s and local testing
        self.is_k8s_environment = self.check_k8s_environment()


    def wrap_extra_fields_in_context(self, record: logging.LogRecord, extra_fields: Optional[Any]) -> None:
        """Wrap context with additional information"""
        ctx: Dict[str, Any] = {}

        if isinstance(extra_fields, dict):
            ctx.update(extra_fields)
        elif extra_fields is not None:
            ctx["value"] = str(extra_fields)

        record.extra_fields = {"context": ctx}
    def log_info(self, message: str, extra_fields: Dict = None):
        """Log info message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.INFO, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        self.wrap_extra_fields_in_context(record, extra_fields)
        logger.handle(record)

    def log_warning(self, message: str, extra_fields: Dict = None):
        """Log warning message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.WARNING, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        self.wrap_extra_fields_in_context(record, extra_fields)
        logger.handle(record)

    def log_error(self, message: str, extra_fields: Dict = None):
        """Log error message with structured data"""
        record = logging.LogRecord(
            name=logger.name, level=logging.ERROR, pathname="", lineno=0,
            msg=message, args=(), exc_info=None
        )
        # allow callers to request that the current exception info be attached
        exc_info_flag = False
        if isinstance(extra_fields, dict) and extra_fields.pop("_exc_info", False):
            exc_info_flag = True
        self.wrap_extra_fields_in_context(record, extra_fields)
        if exc_info_flag:
            record.exc_info = sys.exc_info()
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
                    node_name = f"pg-{ip.replace('.', '-') }"
                    nodes.append((ip, node_name))

        if not nodes:
            self.log_error("No PostgreSQL nodes found in PG_NODES environment variable")
            raise EndpointManagerError("No PostgreSQL nodes configured via PG_NODES")

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
                "kubernetes_environment": self.is_k8s_environment,
                "service": self.rw_service
            })
            return None

        try:
            # Get the endpoint
            endpoint = self.k8s_v1.read_namespaced_endpoints(
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
                    "file_path": filepath,
                    "content_length": len(content)
                })
                return content
        except Exception as e:
            self.log_error("Failed to read file", {
                "file_path": filepath,
                "error": str(e),
                "error_type": type(e).__name__
            })
            raise EndpointManagerError(f"Failed to read file {filepath}: {e}")

    # run_command removed: psycopg driver is used for DB checks; no subprocess fallback required


    def check_postgres_node(self, ip: str, name: str) -> Optional[str]:
        """Check if PostgreSQL node is primary or standby"""
        self.log_info("Checking PostgreSQL node", {"node_name": name, "node_ip": ip})

        # Quick TCP connectivity check (cheap and avoids spawning processes)
        try:
            with socket.create_connection((ip, 5432), timeout=self.connection_timeout):
                pass
        except Exception as e:
            self.log_info("Node status determined", {
                "node_name": name,
                "node_ip": ip,
                "status": "DOWN",
                "connectivity_check": "failed",
                "error": str(e),
                "timeout": self.connection_timeout
            })
            return None

        # Use psycopg (v3) if available for reliable role detection
        recovery_val = None

        if PSYCOPG_AVAILABLE:
            user = os.environ.get('PGUSER', 'repmgr')
            password = os.environ.get('PGPASSWORD', '')
            database = os.environ.get('PGDATABASE', 'repmgr')

            @retry_with_backoff
            def query_recovery():
                conn = psycopg.connect(host=ip, user=user, password=password, dbname=database, connect_timeout=self.connection_timeout)
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT pg_is_in_recovery();')
                        row = cur.fetchone()
                    return row[0] if row is not None else None
                finally:
                    conn.close()

            try:
                recovery_val = query_recovery()
            except Exception as e:
                self.log_warning("DB role check failed", {"node_name": name, "node_ip": ip, "error": str(e)})
                return None

        else:
            # Fallback: use psql subprocess if available in the image (should be rare)
            recovery_cmd = ["psql", "-h", ip, "-t", "-A", "-c", "SELECT pg_is_in_recovery();", "--set", "ON_ERROR_STOP=1"]
            success, stdout, stderr = self.run_command(recovery_cmd, timeout=self.connection_timeout)
            if not success:
                self.log_warning("Node status unknown (psql fallback)", {"node_name": name, "node_ip": ip, "error": stderr})
                return None
            recovery_val = stdout.strip()

        # Normalize result to boolean
        is_in_recovery = None
        if isinstance(recovery_val, bool):
            is_in_recovery = recovery_val
        elif recovery_val is None:
            self.log_warning("Node role unknown (no result)", {"node_name": name, "node_ip": ip})
            return None
        else:
            v = str(recovery_val).strip().lower()
            if v in ("t", "true", "1"):
                is_in_recovery = True
            elif v in ("f", "false", "0"):
                is_in_recovery = False
            else:
                self.log_warning("Node status unknown (unexpected recovery value)", {"node_name": name, "node_ip": ip, "recovery_status": recovery_val})
                return None

        if not is_in_recovery:
            self.log_info("Node status determined", {"node_name": name, "node_ip": ip, "status": "PRIMARY", "recovery_status": str(recovery_val)})
            return 'primary'
        else:
            self.log_info("Node status determined", {"node_name": name, "node_ip": ip, "status": "STANDBY", "recovery_status": str(recovery_val)})
            return 'standby'

    def verify_topology(self, nodes: List[Tuple[str, str]]) -> Dict:
        """Verify the actual topology of discovered nodes with parallel processing"""
        self.log_info("Starting topology verification", {
            "total_nodes": len(nodes),
            "max_workers": self.max_workers,
            "parallel_processing": True
        })

        # Local state for topology discovery
        primary_ip = None
        primary_name = None
        standby_ips = []
        primary_ips_found = []

        # Use ThreadPoolExecutor for parallel node checking
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all node checks
            future_to_node = {
                executor.submit(self.check_postgres_node, ip, name): (ip, name)
                for ip, name in nodes
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_node):
                ip, name = future_to_node[future]
                try:
                    status = future.result(timeout=self.connection_timeout + 5)

                    if status == 'primary':
                        primary_ips_found.append(ip)
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

        # If multiple primaries were detected, treat it as an error and abort updates
        if len(primary_ips_found) > 1:
            self.log_error("Multiple primaries detected — aborting updates", {
                "primary_ips_found": primary_ips_found
            })
            topology['primary_ip'] = None
            topology['primary_name'] = None

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

        # If we're not running in Kubernetes (e.g., local tests), skip actual endpoint updates.
        if not self.is_k8s_environment:
            self.log_info("Skipping endpoint update; not in Kubernetes environment", {"service_name": service_name})
            return True

        if not KUBERNETES_CLIENT_AVAILABLE:
            self.log_error("Kubernetes client not available for endpoint update", {"service_name": service_name})
            return False

        try:
            # Build a plain dict payload to avoid client model incompatibilities across versions
            endpoint_body = {
                "metadata": {
                    "name": service_name,
                    "annotations": {
                        "postgres.discovery/last-topology": topology_signature,
                        "postgres.discovery/last-update": datetime.now(timezone.utc).isoformat()
                    }
                },
                # If there are target IPs, construct subsets with addresses and ports.
                # Otherwise provide empty subsets to indicate no ready endpoints.
                "subsets": [
                    {
                        "addresses": [{"ip": ip} for ip in target_ips],
                        "ports": [{"port": 5432, "protocol": "TCP", "name": "postgresql"}]
                    }
                ] if target_ips else []
            }

            try:
                self.k8s_v1.patch_namespaced_endpoints(
                    name=service_name,
                    namespace=self.namespace,
                    body=endpoint_body
                )
                self.log_info("Endpoint patched successfully", {
                    "service_name": service_name,
                    "description": description,
                    "target_ips": target_ips,
                    "method": "kubernetes_client",
                    "annotations_updated": True
                })
                return True
            except client.exceptions.ApiException as e:
                # If the resource doesn't exist, create it
                if hasattr(e, 'status') and e.status == 404:
                    self.log_warning("Endpoint not found, the controller has no permission to create", {
                        "service_name": service_name,
                        "namespace": self.namespace,
                        "status": e.status,
                        "advice": "Ensure the endpoints exist"
                    })
                    return False
                raise

        except client.exceptions.ApiException as e:
            self.log_error("Kubernetes client endpoint update failed", {
                "service_name": service_name,
                "namespace": self.namespace,
                "status": e.status if hasattr(e, 'status') else 'unknown',
                "advice": "Check the endpoint configuration"
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
        prog="postgres-endpoint-manager.py",
        description="PostgreSQL Endpoint Manager"
    )
    parser.add_argument("--version", action="version", version="1.0.0")
    parser.add_argument("--test", action="store_true", help="Run the test script (test-postgres-endpoint-manager.py)")
    args, unknown = parser.parse_known_args()

    if args.test:
        # Run the separated test script and forward exit code
        test_script = os.path.join(os.path.dirname(__file__), "test-postgres-endpoint-manager.py")
        if not os.path.exists(test_script):
            print(f"Test script not found: {test_script}", file=sys.stderr)
            sys.exit(2)
        try:
            runpy.run_path(test_script, run_name="__main__")
            sys.exit(0)
        except SystemExit as e:
            sys.exit(e.code if isinstance(e.code, int) else 1)

    try:
        manager = PostgreSQLEndpointManager()
        rc = manager.run()
        sys.exit(0 if rc else 1)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        # existing error logging handled inside class; ensure non-zero exit
        sys.exit(1)