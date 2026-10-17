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

    def discover_containers_from_proxmox(self, proxmox_host, proxmox_user, proxmox_password,
                                       proxmox_port=8006, verify_ssl=False):
        """
        Discover containers from Proxmox
        Note: This would require the Proxmox API client library
        For now, we'll return a placeholder implementation
        """
        # In a real implementation, this would use the Proxmox API
        # Example using proxmoxer library:
        # from proxmoxer import ProxmoxAPI
        # proxmox = ProxmoxAPI(proxmox_host, user=proxmox_user, password=proxmox_password,
        #                     port=proxmox_port, verify_ssl=verify_ssl)
        #
        # containers = []
        # for node in proxmox.nodes.get():
        #     node_name = node['node']
        #     for vm in proxmox.nodes(node_name).lxc.get():
        #         containers.append({
        #             'name': vm['name'],
        #             'container_id': str(vm['vmid']),
        #             'status': vm['status'],
        #             'ssh_host': vm.get('net0', {}).get('ip') if vm.get('net0') else None
        #         })
        # return containers

        # Placeholder for now - in real implementation this would connect to Proxmox API
        logger.info("Proxmox container discovery not implemented in this version")
        return []

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