"""
Patch execution engine for LXC containers
"""
import paramiko
import logging
import subprocess
import time
from datetime import datetime
from .database import save_update_history, get_container_by_id

logger = logging.getLogger(__name__)

class PatchEngine:
    """Handles patching operations on containers"""

    def __init__(self):
        pass

    def execute_ssh_command(self, host, port, user, command, timeout=300):
        """Execute a command via SSH"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            # In production, proper key management would be needed
            ssh.connect(host, port=port, username=user, timeout=timeout)

            stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)

            # Read output and error
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')

            ssh.close()

            return {
                'success': True,
                'output': output,
                'error': error
            }
        except Exception as e:
            logger.error(f"SSH command execution failed: {str(e)}")
            return {
                'success': False,
                'output': '',
                'error': str(e)
            }

    def check_updates_available(self, container_id):
        """Check if updates are available for a container"""
        container = get_container_by_id(container_id)
        if not container:
            return None

        if not container['ssh_host']:
            return {'error': 'No SSH host configured'}

        # Execute apt update command to check for updates
        command = "apt-get update"
        result = self.execute_ssh_command(
            container['ssh_host'],
            container['ssh_port'],
            container['ssh_user'],
            command
        )

        if not result['success']:
            return {'error': f"Failed to check updates: {result['error']}"}

        # Parse output to see what packages are available for upgrade
        # This is a simplified approach - in practice, we'd want more detailed parsing
        output = result['output']
        # Look for lines indicating available updates (this is approximate)
        return {
            'success': True,
            'output': output,
            'updates_available': 'upgraded' in output or 'new packages' in output
        }

    def apply_patches(self, container_id, snapshot_before=True):
        """Apply patches to a container"""
        container = get_container_by_id(container_id)
        if not container:
            return {'error': 'Container not found'}

        if not container['ssh_host']:
            return {'error': 'No SSH host configured'}

        start_time = datetime.now()
        packages_updated = []
        error_message = ""

        try:
            # Create snapshot before update (if enabled)
            if snapshot_before and container['snapshot_enabled']:
                logger.info(f"Creating snapshot for {container['name']}")
                # This would require Proxmox API calls in a real implementation
                # Placeholder for now

            # Execute apt update and upgrade commands
            logger.info(f"Applying patches to {container['name']}")

            # Step 1: Update package lists
            command = "apt-get update"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command
            )

            if not result['success']:
                error_message = f"Failed to update package lists: {result['error']}"
                logger.error(error_message)
                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now()
                }

            # Step 2: Check what would be upgraded
            command = "apt-get --dry-run upgrade"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command
            )

            if not result['success']:
                error_message = f"Failed to check packages to upgrade: {result['error']}"
                logger.error(error_message)
                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now()
                }

            # Extract package information from output
            if result['output']:
                lines = result['output'].split('\n')
                for line in lines:
                    if 'upgrade' in line and 'package' in line:
                        packages_updated.append(line.strip())

            # Step 3: Actually perform the upgrade
            command = "apt-get -y upgrade"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=600  # Longer timeout for upgrade operation
            )

            if not result['success']:
                error_message = f"Failed to apply upgrades: {result['error']}"
                logger.error(error_message)

                # Try to rollback if snapshot was created
                if snapshot_before and container['snapshot_enabled']:
                    logger.info(f"Attempting rollback for {container['name']}")
                    # Placeholder for rollback logic in real implementation

                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now()
                }

            end_time = datetime.now()

            # Save update history
            update_data = {
                'container_id': container_id,
                'status': 'success',
                'start_time': start_time,
                'end_time': end_time,
                'packages_updated': '; '.join(packages_updated) if packages_updated else 'None',
                'output': result['output'],
                'error_message': error_message
            }

            save_update_history(update_data)

            logger.info(f"Successfully applied patches to {container['name']}")
            return {
                'success': True,
                'start_time': start_time,
                'end_time': end_time,
                'packages_updated': packages_updated,
                'output': result['output']
            }

        except Exception as e:
            error_message = f"Unexpected error during patching: {str(e)}"
            logger.error(error_message)

            # Save failed update history
            update_data = {
                'container_id': container_id,
                'status': 'failed',
                'start_time': start_time,
                'end_time': datetime.now(),
                'packages_updated': '; '.join(packages_updated) if packages_updated else 'None',
                'output': '',
                'error_message': error_message
            }

            save_update_history(update_data)

            return {
                'success': False,
                'error': error_message,
                'start_time': start_time,
                'end_time': datetime.now()
            }

    def apply_patches_batch(self, container_ids):
        """Apply patches to multiple containers"""
        results = []

        for container_id in container_ids:
            result = self.apply_patches(container_id)
            results.append({
                'container_id': container_id,
                'result': result
            })

        return results

# Global instance for use in the application
patch_engine = PatchEngine()