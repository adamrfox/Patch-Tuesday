"""
Patch execution engine for LXC containers.

Handles SSH-based patch operations, including:
- Exit-code-aware command execution
- Dry-run update checking with conditional skipping
- Proxmox snapshot creation / rollback / cleanup
- Update history logging (for all outcomes)
"""
import paramiko
import logging
import subprocess
import time
from datetime import datetime
from .database import save_update_history, get_container_by_id

logger = logging.getLogger(__name__)


def _count_upgradable(dry_run_output: str) -> int:
    """Count packages that would be upgraded from apt-get --dry-run output.

    The dry-run output has a summary line like::

        The following packages will be upgraded: NNN

    Returns 0 when nothing is upgradable (or when the line cannot be found).
    """
    if not dry_run_output:
        return 0
    for line in dry_run_output.splitlines():
        stripped = line.strip()
        # Typical output: "The following packages will be upgraded: 5"
        if stripped.startswith("The following packages will be upgraded"):
            parts = stripped.split(":")
            if len(parts) >= 2 and parts[1].strip().isdigit():
                return int(parts[1].strip())
    # Fallback: also check "The following packages will be REMOVED:" (zero means no regular upgrades)
    for line in dry_run_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("The following packages will be removed"):
            parts = stripped.split(":")
            if len(parts) >= 2 and parts[1].strip().isdigit():
                count = int(parts[1].strip())
                logger.debug(f"Packages to remove (no upgrades): {count}")
                return 0
    return 0


class PatchEngine:
    """Handles patching operations on containers"""

    def __init__(self):
        pass

    def execute_ssh_command(self, host, port, user, command, timeout=300):
        """Execute a command via SSH and return its exit code.

        Returns:
            dict with keys:
                success (bool): True iff SSH layer succeeded AND command exit code is 0
                exit_code (int): The command's exit status
                output (str): stdout from the command
                error (str): stderr from the command
        """
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, port=port, username=user, timeout=timeout)

            stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)

            # Read output and error
            output = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')

            # IMPORTANT: read the exit code AFTER closing (or before, both work).
            # recv_exit_status() blocks until the remote command finishes, which
            # guarantees a valid exit code.
            exit_code = stdout.channel.recv_exit_status()

            ssh.close()

            success = (exit_code == 0)
            return {
                'success': success,
                'exit_code': exit_code,
                'output': output,
                'error': error,
            }
        except Exception as e:
            logger.error(f"SSH command execution failed: {str(e)}")
            return {
                'success': False,
                'exit_code': -1,
                'output': '',
                'error': str(e),
            }

    def check_updates_available(self, container_id):
        """Check if updates are available for a container.

        Returns:
            dict with keys: success (bool), output (str), updates_available (int), error (str)?
        """
        container = get_container_by_id(container_id)
        if not container:
            return None

        if not container['ssh_host']:
            return {'error': 'No SSH host configured'}

        # Execute apt update to refresh package lists
        command = "apt-get update"
        result = self.execute_ssh_command(
            container['ssh_host'],
            container['ssh_port'],
            container['ssh_user'],
            command,
            timeout=300
        )

        if not result['success']:
            # Save failure to DB even when just checking
            error_msg = f"Failed to update package lists (exit code {result.get('exit_code', '?')}): {result['error']}"
            logger.error(error_msg)
            return {'error': error_msg, 'updates_available': 0}

        # Parse how many packages would be upgraded using the dry-run format
        command = "apt-get --dry-run upgrade"
        result = self.execute_ssh_command(
            container['ssh_host'],
            container['ssh_port'],
            container['ssh_user'],
            command,
            timeout=300
        )

        if not result['success']:
            error_msg = f"Failed to check packages (exit code {result.get('exit_code', '?')}): {result['error']}"
            logger.error(error_msg)
            return {'error': error_msg, 'updates_available': 0}

        # Properly parse the "The following packages will be upgraded: N" line
        count = _count_upgradable(result['output'])

        return {
            'success': True,
            'output': result['output'],
            'updates_available': count,
        }

    def apply_patches(self, container_id, snapshot_before=True):
        """Apply patches to a container.

        Args:
            container_id: Database row ID of the container.
            snapshot_before: If True and the container has proxmox_node set,
                             create a Proxmox snapshot before patching.

        Returns:
            dict with keys: success (bool), packages_updated (list[str]),
                            output (str), error (str)?, start_time, end_time, skipped (bool)?
        """
        container = get_container_by_id(container_id)
        if not container:
            return {'error': 'Container not found'}

        if not container['ssh_host']:
            return {'error': 'No SSH host configured'}

        start_time = datetime.now()
        packages_updated = []
        error_message = ""

        try:
            # ---------------------------------------------------------------
            # Snapshot before update (if enabled and we can locate the node)
            # ---------------------------------------------------------------
            snapshot_name = None
            proxmox_node = None
            proxmox_vmid = None
            if snapshot_before and container.get('snapshot_enabled'):
                proxmox_node, proxmox_vmid = _find_node_for_container(container)
                if proxmox_node:
                    from .proxmox_api import create_lxc_snapshot
                    snapshot_name = f"ptu_{int(time.time())}"
                    snap_result = create_lxc_snapshot(
                        proxmox_node, proxmox_vmid, snapshot_name
                    )
                    if snap_result['success']:
                        logger.info(f"Proxmox snapshot '{snapshot_name}' created for {container['name']}")
                    else:
                        logger.warning(
                            f"Snapshot creation failed for {container['name']}: "
                            f"{snap_result.get('error', 'unknown')}"
                        )
                        snapshot_name = None  # disable rollback if snapshot failed
                else:
                    logger.info(
                        f"No Proxmox node info for {container['name']} — skipping snapshot"
                    )

            # ---------------------------------------------------------------
            # Step 1: Update package lists
            # ---------------------------------------------------------------
            logger.info(f"Applying patches to {container['name']}")
            command = "apt-get update"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=300
            )

            if not result['success']:
                error_message = (
                    f"Failed to update package lists "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{result['error']}"
                )
                logger.error(error_message)
                # Save failure to DB (was missing before — now fixed)
                save_update_history({
                    'container_id': container_id,
                    'status': 'failed',
                    'start_time': start_time,
                    'end_time': datetime.now(),
                    'packages_updated': 'None',
                    'output': result['output'],
                    'error_message': error_message,
                })
                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now(),
                }

            # ---------------------------------------------------------------
            # Step 2: Dry-run — check what would be upgraded
            # ---------------------------------------------------------------
            command = "apt-get --dry-run upgrade"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=300
            )

            if not result['success']:
                error_message = (
                    f"Failed to check packages "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{result['error']}"
                )
                logger.error(error_message)
                save_update_history({
                    'container_id': container_id,
                    'status': 'failed',
                    'start_time': start_time,
                    'end_time': datetime.now(),
                    'packages_updated': 'None',
                    'output': result['output'],
                    'error_message': error_message,
                })
                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now(),
                }

            # Parse the summary line: "The following packages will be upgraded: N"
            count = _count_upgradable(result['output'])
            if count > 0:
                # Also extract individual package names from lines like:
                #   Inst libfoo [1.0] (1.1 Ubuntu) [libbar]
                for line in result['output'].splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Inst "):
                        packages_updated.append(stripped)

            # ---------------------------------------------------------------
            # Conditional skip — no updates needed
            # ---------------------------------------------------------------
            if count == 0:
                logger.info(f"No updates available for {container['name']}")
                save_update_history({
                    'container_id': container_id,
                    'status': 'success',
                    'start_time': start_time,
                    'end_time': datetime.now(),
                    'packages_updated': 'None (nothing to upgrade)',
                    'output': '',
                    'error_message': '',
                })
                # Clean up any snapshot we created before discovering nothing was needed
                if snapshot_name and proxmox_node:
                    try:
                        from .proxmox_api import delete_lxc_snapshot
                        delete_lxc_snapshot(proxmox_node, proxmox_vmid, snapshot_name)
                    except Exception as e:
                        logger.warning(f"Could not clean up snapshot {snapshot_name}: {e}")
                return {
                    'success': True,
                    'start_time': start_time,
                    'end_time': datetime.now(),
                    'packages_updated': [],
                    'output': '',
                    'skipped': True,
                    'message': f"No updates available ({count} packages)",
                }

            logger.info(f"{count} package(s) will be updated for {container['name']}")

            # ---------------------------------------------------------------
            # Step 3: Actually perform the upgrade
            # ---------------------------------------------------------------
            command = "apt-get -y upgrade"
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=600  # Longer timeout for upgrade operation
            )

            if not result['success']:
                error_message = (
                    f"Failed to apply upgrades "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{result['error']}"
                )
                logger.error(error_message)

                # Rollback via Proxmox if snapshot was created
                if snapshot_name and proxmox_node:
                    try:
                        from .proxmox_api import rollback_lxc_snapshot
                        rb = rollback_lxc_snapshot(proxmox_node, proxmox_vmid, snapshot_name)
                        if rb['success']:
                            logger.info(
                                f"Rollback successful for {container['name']} "
                                f"to snapshot {snapshot_name}"
                            )
                            error_message += " [rolled back]"
                        else:
                            logger.error(
                                f"Rollback FAILED for {container['name']}: "
                                f"{rb.get('error', 'unknown')}"
                            )
                    except Exception as e:
                        logger.error(f"Rollback exception for {container['name']}: {e}")

                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now(),
                }

            # ---------------------------------------------------------------
            # Success — clean up snapshot
            # ---------------------------------------------------------------
            end_time = datetime.now()

            if snapshot_name and proxmox_node:
                try:
                    from .proxmox_api import delete_lxc_snapshot
                    delete_lxc_snapshot(proxmox_node, proxmox_vmid, snapshot_name)
                    logger.info(f"Cleaned up snapshot {snapshot_name} for {container['name']}")
                except Exception as e:
                    logger.warning(
                        f"Could not clean up snapshot {snapshot_name}: {e}"
                    )

            # Save update history
            save_update_history({
                'container_id': container_id,
                'status': 'success',
                'start_time': start_time,
                'end_time': end_time,
                'packages_updated': '; '.join(packages_updated) if packages_updated else 'None (empty upgrade)',
                'output': result['output'],
                'error_message': error_message,
            })

            logger.info(f"Successfully applied patches to {container['name']} ({len(packages_updated)} package(s))")
            return {
                'success': True,
                'start_time': start_time,
                'end_time': end_time,
                'packages_updated': packages_updated,
                'output': result['output'],
            }

        except Exception as e:
            error_message = f"Unexpected error during patching: {str(e)}"
            logger.error(error_message)

            # Save failed update history
            save_update_history({
                'container_id': container_id,
                'status': 'failed',
                'start_time': start_time,
                'end_time': datetime.now(),
                'packages_updated': '; '.join(packages_updated) if packages_updated else 'None',
                'output': '',
                'error_message': error_message,
            })

            return {
                'success': False,
                'error': error_message,
                'start_time': start_time,
                'end_time': datetime.now(),
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
