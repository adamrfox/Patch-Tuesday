"""
Container management for Proxmox LXC containers
"""
import paramiko
import logging
from .database import get_all_containers, get_container_by_id, save_container
# We don't actually need to import execute_ssh_command here - it's used in patch_engine directly
# The error message may have been misleading or from a cached version
from .patch_engine import patch_engine

logger = logging.getLogger(__name__)

class ContainerManager:
    """Manages communication with LXC containers"""

    def __init__(self):
        self.containers = []

    def discover_containers_from_proxmox(self, proxmox_host, proxmox_user=None,
                                         proxmox_password=None, proxmox_port=8006,
                                         verify_ssl=False, token_name=None, token_value=None):
        """Discover containers from Proxmox via REST API.

        Supports both API token auth (user@realm!keyname) and password auth.

        Args:
            proxmox_host: Cluster hostname or IP address.
            proxmox_user: Username (may use 'user@realm!keyname' format).
            proxmox_password: Password for fallback auth.
            proxmox_port: API port, default 8006.
            verify_ssl: Verify SSL certificates.
            token_name: API token key name.
            token_value: API token secret value.

        Returns:
            List of dicts with name, container_id, status, node, primary_ip, etc.
        """
        if not proxmox_host:
            logger.warning("discover_containers_from_proxmox called without proxmox_host")
            return []

        from . import proxmox_api as pa

        try:
            discovered = pa.discover_proxmox_containers(
                proxmox_host=proxmox_host,
                proxmox_user=proxmox_user,
                proxmox_password=proxmox_password,
                proxmox_port=proxmox_port,
                verify_ssl=verify_ssl,
                token_name=token_name,
                token_value=token_value,
            )
            return discovered
        except ValueError as e:
            logger.warning(f"Cannot discover: {e}")
            return []
        except Exception as e:
            logger.error(f"Error discovering containers from Proxmox: {e}")
            return []

    def import_proxmox_containers(self, proxmox_containers, ssh_user='root', ssh_port=22):
        """Save discovered containers into the database.

        Skips containers that already exist (same container_id or name).

        Args:
            proxmox_containers: List of dicts from discover_containers_from_proxmox().
            ssh_user: Default SSH user.
            ssh_port: Default SSH port.

        Returns:
            dict with 'imported', 'skipped', 'errors' keys.
        """
        if not proxmox_containers:
            logger.warning("import_proxmox_containers called with no containers")
            return {'imported': 0, 'skipped': 0, 'errors': []}

        from . import proxmox_api as pa
        try:
            result = pa.import_proxmox_containers(
                proxmox_containers, ssh_user=ssh_user, ssh_port=ssh_port
            )
            return result
        except Exception as e:
            logger.error(f"Error importing containers from Proxmox: {e}")
            return {'imported': 0, 'skipped': 0, 'errors': [str(e)]}

    def add_container(self, name, container_id, ssh_host=None, ssh_port=22, ssh_user='root',
                     snapshot_enabled=True):
        """Add a new container manually"""
        container_data = {
            'name': name,
            'container_id': container_id,
            'status': 'unknown',
            'ssh_host': ssh_host,
            'ssh_port': ssh_port,
            'ssh_user': ssh_user,
            'snapshot_enabled': snapshot_enabled
        }

        # Try to verify connection if SSH host is provided
        if ssh_host:
            try:
                connected = self.test_ssh_connection(ssh_host, ssh_port, ssh_user)
                container_data['status'] = 'connected' if connected else 'disconnected'
            except Exception as e:
                logger.error(f"Error testing connection to {name}: {str(e)}")
                container_data['status'] = 'error'
        else:
            container_data['status'] = 'configured'

        save_container(container_data)
        return container_data

    def test_ssh_connection(self, host, port, user):
        """Test SSH connection to container"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            # Note: In production, proper key management would be needed
            ssh.connect(host, port=port, username=user, timeout=10)
            ssh.close()
            return True
        except Exception as e:
            logger.error(f"SSH connection failed to {host}: {str(e)}")
            return False

    def get_container_status(self, container_id):
        """Get current status of a container"""
        # This would be enhanced with actual Proxmox API calls or SSH checks
        container = get_container_by_id(container_id)
        if not container:
            return None

        # For now, return the stored status
        return {
            'id': container['id'],
            'name': container['name'],
            'status': container['status'],
            'container_id': container['container_id']
        }

    def get_all_containers(self):
        """Get all managed containers"""
        return get_all_containers()

    def update_container_status(self, container_id, status):
        """Update container status in database"""
        container = get_container_by_id(container_id)
        if container:
            container_data = {
                'id': container['id'],
                'name': container['name'],
                'container_id': container['container_id'],
                'status': status,
                'last_updated': None,  # Will be set by save_container
                'updated_needs': False
            }
            save_container(container_data)

    def remove_container(self, container_id):
        """Remove a container from management"""
        # In a real implementation, we'd also clean up any associated schedules or data
        return True

# Global instance for use in the application
container_manager = ContainerManager()