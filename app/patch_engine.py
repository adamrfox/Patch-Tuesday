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
import os
import socket
import threading
import tempfile
import ipaddress
from datetime import datetime
from .database import save_update_history, get_container_by_id

logger = logging.getLogger(__name__)
_LIVE_LOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'live_patch.log')


def _count_upgradable(dry_run_output: str) -> int:
    """Count packages that would be upgraded from apt-get --dry-run output.

    Two formats exist depending on the package count:

    Format A (small list, all on one line)::

        The following packages will be upgraded: 5

    Format B (long list, split across lines)::

        The following packages will be upgraded:
          package1
          package2
        3 upgraded, 0 newly installed, ...

    Returns 0 when nothing is upgradable (or when the line cannot be found).
    """
    if not dry_run_output:
        return 0
    for line in dry_run_output.splitlines():
        stripped = line.strip()
        # Format A: "The following packages will be upgraded: 5"
        if stripped.startswith("The following packages will be upgraded"):
            parts = stripped.split(":")
            if len(parts) >= 2 and parts[1].strip().isdigit():
                return int(parts[1].strip())

    # Format B fallback: look for the summary line "N upgraded, ..."
    import re as _re
    for line in dry_run_output.splitlines():
        m = _re.search(r'(\d+)\s+upgraded', line.strip())
        if m:
            return int(m.group(1))

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


def _get_local_ips() -> set:
    """Return the set of IP addresses assigned to this host (as strings).

    Combines loopback, hostname-derived addresses, and the IPv4 addresses
    actually assigned to network interfaces. The interface scan matters on
    Proxmox systems where /etc/hosts maps the hostname to 127.0.1.1 instead
    of the real NIC address.
    """
    import fcntl
    import struct

    ips = set()
    try:
        ips.add(ipaddress.ip_loopback.compressed)
        ips.add('::1')
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except Exception:
        pass
    # IPv4 address per non-loopback interface (SIOCGIFADDR = 0x8915)
    try:
        for ifname in os.listdir('/sys/class/net'):
            if ifname == 'lo':
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                packed = struct.pack('256s', ifname.encode()[:15])
                ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24])
                ips.add(ip)
                s.close()
            except Exception:
                pass
    except Exception:
        pass
    return ips


def _env_local_hosts() -> set:
    """Hostnames/IPs explicitly declared as local via PTU_LOCAL_HOSTS.

    Needed when the app runs inside a network namespace (Docker): the host's
    real IP (e.g. 192.168.86.190) is not visible inside the container, whose
    own IP is the bridge address instead. Set e.g. PTU_LOCAL_HOSTS=192.168.86.190
    in docker-compose.yml.
    """
    raw = os.environ.get('PTU_LOCAL_HOSTS', '')
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def is_local_host(host) -> bool:
    """Return True if the given host/IP refers to this machine.

    Used to detect the container this app lives on (or its host) so that
    patch commands can be executed locally instead of over SSH — SSH to
    itself may not be available (no sshd / no keys), while local execution
    always works.
    """
    if not host:
        return False
    h = str(host).strip().strip('[]').lower()
    if not h:
        return False
    # Explicit declaration wins (e.g. the Docker host's IP, invisible from
    # inside the container's network namespace)
    if h in _env_local_hosts():
        return True
    if h == '0.0.0.0':
        return True
    try:
        addr = ipaddress.ip_address(h)
        if addr.is_loopback or addr.is_unspecified:
            return True
        return str(addr) in _get_local_ips()
    except ValueError:
        pass
    if h == 'localhost':
        return True
    # Hostname: resolve and compare against this host's own addresses
    try:
        local_ips = _get_local_ips()
        for info in socket.getaddrinfo(h, None):
            if info[4][0] in local_ips:
                return True
    except Exception:
        pass
    return False


class PatchEngine:
    """Handles patching operations on containers"""

    def __init__(self):
        self._active = set()

    def register_start(self, container_id):
        self._active.add(container_id)

    def register_done(self, container_id):
        self._active.discard(container_id)

    def is_running(self, container_id):
        return container_id in self._active

    @staticmethod
    def _resolve_key_files(private_key_path=None):
        """Resolve the SSH key files to try for authentication.

        If an explicit key path is configured, use it — but if it does not
        exist (e.g. a host-side path stored while running inside Docker),
        warn and fall back to the default identity keys in common
        locations instead of failing with a raw ENOENT from paramiko.

        Returns a list of existing key file paths (may be empty).
        """
        if private_key_path:
            if os.path.exists(private_key_path):
                return [private_key_path]
            logger.warning(
                f"Configured SSH key {private_key_path!r} not found; "
                "falling back to default identity keys"
            )

        home = os.path.expanduser('~')
        key_dirs = [f'{home}/.ssh'] + \
                   ([f'/home/{u}/.ssh' for u in ['midway', 'appuser', 'flask']] if home == '/root' else [])
        candidates = []
        for d in key_dirs:
            for k in ('id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa'):
                p = f'{d}/{k}'
                if os.path.exists(p):
                    candidates.append(p)
        return candidates

    # ---------------------------------------------------------------
    # Live log file — see live_patch_<id>.log for real-time patch output
    # ---------------------------------------------------------------

    @staticmethod
    def _start_live_log(container: dict) -> tuple:
        """Create the local and remote log files.

        Returns a tuple of (local_log_path, remote_log_path).
        The remote path is used by tee on each step; at the end it is fetched back.
        """
        proxmox_id = str(container['container_id'])
        local_path = os.path.join(os.path.dirname(_LIVE_LOG_PATH), f'live_patch_{proxmox_id}.log')
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, 'w') as f:
            f.write(f"=== Patch run started at {datetime.now().isoformat()} ===\n")
            f.write(
                f"Container : {container['name']}  (ID={proxmox_id})\n"
                f"Host      : {container['ssh_host']}:{container['ssh_port']}\n"
                f"User      : {container.get('ssh_user', 'unknown')}\n"
            )
            f.write("---\n\n")
        remote_path = '/tmp/ptu_update.log'
        return local_path, remote_path

    def _fetch_remote_log(self, host, port, user, remote_path, private_key_path=None) -> str:
        """Download a file from the remote container via SFTP.

        Returns the file content as a string.
        """
        try:
            transport = paramiko.Transport((host, port))
            key_files = self._resolve_key_files(private_key_path)
            key_kwargs = {'key_filename': key_files} if key_files else {}

            transport.connect(username=user, **key_kwargs)
            sftp = paramiko.SFTPClient.from_transport(transport)
            content = sftp.open(remote_path).read().decode('utf-8', errors='replace')
            sftp.close()
            transport.close()
            return content
        except Exception:
            logger.warning(f"Could not fetch remote log from {host}")
            return ''

    def _cleanup_remote_log(self, host, port, user, remote_path, private_key_path=None):
        """Delete the remote temp log file."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kwargs = {'timeout': 5}
            key_files = self._resolve_key_files(private_key_path)
            if key_files:
                connect_kwargs['key_filename'] = key_files

            ssh.connect(host, port=port, username=user, **connect_kwargs)
            ssh.exec_command(f'rm -f {remote_path}')
            ssh.close()
        except Exception:
            logger.warning(f"Could not clean up remote log from {host}")

    def _execute_local_command(self, command, timeout=300, on_line=None):
        """Execute a command on this host directly (no SSH).

        Used when the target container is the one this app runs on — the
        command is local, so run it with subprocess. Returns the same dict
        contract as execute_ssh_command. Note: no '_remote_log' key is set,
        so _flush_remote_log() becomes a no-op (there is nothing remote).
        """
        try:
            if on_line is not None:
                # Streaming path: run with a merged output pipe and forward
                # each line to the live log as it arrives.
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                chunks = []
                for line in proc.stdout:
                    chunks.append(line)
                    try:
                        on_line(line.rstrip('\r\n'))
                    except Exception:
                        pass
                proc.wait(timeout=timeout)
                output = ''.join(chunks)
                return {
                    'success': proc.returncode == 0,
                    'exit_code': proc.returncode,
                    'output': output,
                    'error': '',
                }
            else:
                completed = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return {
                    'success': completed.returncode == 0,
                    'exit_code': completed.returncode,
                    'output': completed.stdout,
                    'error': completed.stderr,
                }
        except subprocess.TimeoutExpired:
            logger.error(f"Local command timed out after {timeout}s: {command}")
            return {
                'success': False,
                'exit_code': -1,
                'output': '',
                'error': f'Local command timed out after {timeout}s',
            }
        except Exception as e:
            logger.error(f"Local command execution failed: {str(e)}")
            return {
                'success': False,
                'exit_code': -1,
                'output': '',
                'error': str(e),
            }

    def execute_ssh_command(self, host, port, user, command, timeout=300, private_key_path=None, on_line=None):
        """Execute a command via SSH and return its exit code.

        Args:
            host/port/user: SSH connection details.
            command: The remote command to run.
            timeout: Connection and read timeout in seconds.
            private_key_path: Optional path to an SSH private key.
            on_line: If provided, called with (text, 'stdout'/'stderr') for each
                     line as it arrives, enabling live progress output.

        Returns:
            dict with keys:
                success (bool): True iff SSH layer succeeded AND command exit code is 0
                exit_code (int): The command's exit status
                output (str): stdout from the command
                error (str): stderr from the command
        """
        # Local target (this app's own container/host): skip SSH entirely
        if is_local_host(host):
            logger.info(f"Target {host} is local — executing without SSH: {command}")
            return self._execute_local_command(command, timeout=timeout, on_line=on_line)
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Build auth kwargs — prefer configured key, fall back to default SSH keys
            connect_kwargs = {'timeout': timeout}
            key_files = self._resolve_key_files(private_key_path)
            if key_files:
                connect_kwargs['key_filename'] = key_files

            ssh.connect(host, port=port, username=user, **connect_kwargs)

            stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)

            # Use a threading.Lock so concurrent reads don't interleave log output
            _line_lock = threading.Lock()

            if on_line is not None:
                # --- Streaming with remote log via tee.
                # With get_pty=True we CANNOT use stdout.read() — the PTY buffer
                # fills up and blocks the remote command (hang). Instead we wrap
                # the command in `tee` so output goes to both a remote file AND
                # produces normal stdout/stderr for us to poll incrementally.
                remote_log = '/tmp/ptu_update.log'
                wrapper_cmd = (
                    f"{command} 2>&1 | tee {remote_log}; "
                    f"echo \"PTU_EXIT=$?\""
                )

                stdin, stdout, stderr = ssh.exec_command(wrapper_cmd, get_pty=True)

                # Close stdin immediately so the remote side knows we're done.
                stdin.close()

                output_buf = ''
                exit_code = None
                timeout_at = time.monotonic() + 7200  # safety net: 2h max
                while exit_code is None and time.monotonic() < timeout_at:
                    if stdout.channel.exit_status_ready():
                        exit_code = stdout.channel.recv_exit_status()
                        # Drain any remaining buffered data
                        while True:
                            try:
                                chunk = stdout.channel.recv(4096).decode('utf-8', errors='replace')
                                if not chunk:
                                    break
                                for line in chunk.splitlines():
                                    on_line(line)
                                output_buf += chunk
                            except Exception:
                                break
                        break

                    # Poll for data — don't block
                    if stdout.channel.recv_ready():
                        chunk = stdout.channel.recv(65536).decode('utf-8', errors='replace')
                        if chunk:
                            for line in chunk.splitlines(True):  # keep newlines
                                on_line(line.rstrip('\r\n'))
                            output_buf += chunk
                        else:
                            time.sleep(0.1)
                    else:
                        time.sleep(0.1)

                # After draining, fetch the remote tee log for complete output.
                # recv() on PTY may miss data that flowed through the pipe to
                # tee before exit_status_ready fires — the remote file is the
                # source of truth. Also extract the real exit code from PTU_EXIT
                # since the pipeline (cmd | tee) returns tee's exit code (0),
                # not the command's exit code.
                try:
                    full_output = ''
                    with open(remote_log, 'r') as rf:
                        full_output = rf.read()
                except Exception:
                    full_output = output_buf  # fallback

                real_exit = exit_code or -1
                for line in reversed(full_output.splitlines()):
                    stripped = line.strip()
                    if stripped.startswith('PTU_EXIT='):
                        try:
                            real_exit = int(stripped.split('=')[1].strip())
                        except (ValueError, IndexError):
                            pass
                        break

                # Strip PTU_EXIT marker lines from output for cleanliness
                clean_output = '\n'.join(
                    l for l in full_output.splitlines()
                    if not l.strip().startswith('PTU_EXIT=')
                )

                ssh.close()

                success = (real_exit == 0) if real_exit is not None else False
                return {
                    'success': success,
                    'exit_code': real_exit,
                    'output': clean_output,
                    'error': '',
                    '_remote_log': remote_log,
                }
            else:
                # Read output and error (non-streaming path)
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

        # Execute apt update to refresh package lists (requires sudo)
        command = "sudo apt-get update"
        result = self.execute_ssh_command(
            container['ssh_host'],
            container['ssh_port'],
            container['ssh_user'],
            command,
            timeout=300,
            private_key_path=container.get('ssh_private_key_path'),
        )

        if not result['success']:
            # apt-get outputs errors to stdout too — combine both for visibility
            detail = result['output'] or result['error']
            error_msg = f"Failed to update package lists (exit code {result.get('exit_code', '?')}): {detail}"
            logger.error(error_msg)
            return {'error': error_msg, 'updates_available': 0}

        # Parse how many packages would be upgraded using the dry-run format
        command = "sudo apt-get --dry-run upgrade"
        result = self.execute_ssh_command(
            container['ssh_host'],
            container['ssh_port'],
            container['ssh_user'],
            command,
            timeout=300,
            private_key_path=container.get('ssh_private_key_path'),
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

        # ------------------------------------------------------------------
        # Early abort for stopped containers — check live Proxmox status
        # ------------------------------------------------------------------
        try:
            from .proxmox_api import _find_node_for_container, get_lxc_container_status
            proxmox_node, proxmox_vmid = _find_node_for_container(container)
            if proxmox_node and proxmox_vmid:
                status_result = get_lxc_container_status(proxmox_node, proxmox_vmid)
                if status_result['success'] and status_result.get('status') == 'stopped':
                    logger.info(f"Container {container['name']} is stopped on Proxmox — skipping")
                    save_update_history({
                        'container_id': container_id,
                        'status': 'skipped',
                        'start_time': datetime.now(),
                        'end_time': datetime.now(),
                        'packages_updated': 'None (container stopped)',
                        'output': '',
                        'error_message': f'Container "{container["name"]}" is stopped in Proxmox — skipped',
                    })
                    return {
                        'success': False,
                        'error': f'Container "{container["name"]}" is stopped',
                        'start_time': datetime.now(),
                        'end_time': datetime.now(),
                        'skipped': True,  # signal to frontend / dashboard
                    }
        except Exception:
            # If live status check fails, fall through to SSH attempt
            pass

        self.register_start(container_id)
        start_time = datetime.now()
        packages_updated = []
        error_message = ""

        # Start live log file — the user can tail -f it for real-time output
        try:
            local_log_path, remote_log_path = self._start_live_log(container)
        except Exception:
            local_log_path = None
            remote_log_path = None

        def _log(line, kind):
            """Write a line to the live log and Python logger."""
            if local_log_path is not None:
                try:
                    with open(local_log_path, 'a') as f:
                        f.write(f'[{kind.upper():5s}] {line}\n')
                except Exception:
                    pass
            # Also route through standard logging for ops team logs
            logger.info(f"[live-log {kind}] {line}")

        def _flush_remote_log(result):
            """Fetch the remote tee log, append to local file, clean up remote.

            Must NEVER raise — callers save history AFTER this and must not
            be prevented from doing so by a failed flush.
            """
            try:
                if result is None:
                    return
                remote_log = result.get('_remote_log')
                if not remote_log:
                    return
                content = self._fetch_remote_log(
                    container['ssh_host'],
                    container['ssh_port'],
                    container['ssh_user'],
                    remote_log,
                    private_key_path=container.get('ssh_private_key_path'),
                )
                if content and local_log_path is not None:
                    with open(local_log_path, 'a') as f:
                        f.write(f'\n--- Remote log ({len(content)} bytes) ---\n')
                        f.write(content)
                        if not content.endswith('\n'):
                            f.write('\n')
                        f.write('--- end remote log ---\n\n')
            except Exception:
                logger.warning('Could not fetch remote tee log', exc_info=True)
            try:
                self._cleanup_remote_log(
                    container['ssh_host'],
                    container['ssh_port'],
                    container['ssh_user'],
                    remote_log if result else None,
                    private_key_path=container.get('ssh_private_key_path'),
                )
            except Exception:
                pass

        try:
            # ---------------------------------------------------------------
            # Snapshot before update (if enabled and we can locate the node)
            # ---------------------------------------------------------------
            snapshot_name = None
            proxmox_node = None
            proxmox_vmid = None
            if snapshot_before and container['snapshot_enabled']:
                from .proxmox_api import (
                    create_lxc_snapshot,
                    delete_lxc_snapshot,
                    rollback_lxc_snapshot,
                    _find_node_for_container,
                )
                proxmox_node, proxmox_vmid = _find_node_for_container(container)
                if proxmox_node:
                    snapshot_name = f"ptu_{int(time.time())}"
                    _log(f"Creating Proxmox snapshot (name: {snapshot_name})", "header")
                    snap_result = create_lxc_snapshot(
                        proxmox_node, proxmox_vmid, snapshot_name
                    )
                    if snap_result['success']:
                        _log(f"Proxmox snapshot '{snapshot_name}' created", "snapshot")
                        logger.info(f"Proxmox snapshot '{snapshot_name}' created for {container['name']}")
                    else:
                        _log(f"Snapshot creation failed: {snap_result.get('error', 'unknown')}", "snapshot_err")
                        logger.warning(
                            f"Snapshot creation failed for {container['name']}: "
                            f"{snap_result.get('error', 'unknown')}"
                        )
                        snapshot_name = None  # disable rollback if snapshot failed
                else:
                    _log(f"No Proxmox node info — skipping snapshot", "snapshot_skip")
                    logger.info(
                        f"No Proxmox node info for {container['name']} — skipping snapshot"
                    )

            # ---------------------------------------------------------------
            # Step 1: Update package lists
            # ---------------------------------------------------------------
            logger.info(f"Applying patches to {container['name']}")
            command = "sudo apt-get update"
            _log("[Step 1/3] sudo apt-get update", "header")
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=300,
                private_key_path=container.get('ssh_private_key_path'),
                on_line=lambda line: _log(line, 'stdout'),
            )

            if not result['success']:
                # apt-get (and some tools) send warnings/errors to stdout — show both
                detail = result['output'] or result['error']
                error_message = (
                    f"Failed to update package lists "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{detail}"
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
                self.register_done(container_id)
                _flush_remote_log(result)
                _log("[DONE] Failed — apt-get update ({})".format(container['name']), "footer")
                return {
                    'success': False,
                    'error': error_message,
                    'start_time': start_time,
                    'end_time': datetime.now(),
                }

            # ---------------------------------------------------------------
            # Step 2: Dry-run — check what would be upgraded
            # ---------------------------------------------------------------
            command = "sudo apt-get --dry-run upgrade"
            _log("[Step 2/3] sudo apt-get --dry-run upgrade", "header")
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=300,
                private_key_path=container.get('ssh_private_key_path'),
                on_line=lambda line: _log(line, 'stdout'),
            )

            if not result['success']:
                # combine stdout + stderr for full apt output
                detail = result['output'] or result['error']
                error_message = (
                    f"Failed to check packages "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{detail}"
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
                self.register_done(container_id)
                _flush_remote_log(result)
                _log("[DONE] Failed — dry-run check ({})".format(container['name']), "footer")
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
                        _log(f"Cleaned up snapshot '{snapshot_name}' (no updates needed)", "snapshot")
                    except Exception as e:
                        _log(f"Could not clean up snapshot '{snapshot_name}': {e}", "snapshot_err")
                        logger.warning(f"Could not clean up snapshot {snapshot_name}: {e}")
                self.register_done(container_id)
                _flush_remote_log(result)
                _log("[DONE] Skipped — no updates ({})".format(container['name']), "footer")
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
            command = "sudo apt-get -y upgrade"
            _log("[Step 3/3] sudo apt-get -y upgrade", "header")
            result = self.execute_ssh_command(
                container['ssh_host'],
                container['ssh_port'],
                container['ssh_user'],
                command,
                timeout=600,  # Longer timeout for upgrade operation
                private_key_path=container.get('ssh_private_key_path'),
                on_line=lambda line: _log(line, 'stdout'),
            )

            if not result['success']:
                detail = result['output'] or result['error']
                error_message = (
                    f"Failed to apply upgrades "
                    f"(exit code {result.get('exit_code', '?')}): "
                    f"{detail}"
                )
                logger.error(error_message)

                # Rollback via Proxmox if snapshot was created
                if snapshot_name and proxmox_node:
                    try:
                        from .proxmox_api import rollback_lxc_snapshot
                        rb = rollback_lxc_snapshot(proxmox_node, proxmox_vmid, snapshot_name)
                        if rb['success']:
                            _log(f"Rollback to snapshot '{snapshot_name}' succeeded", "snapshot")
                            logger.info(
                                f"Rollback successful for {container['name']} "
                                f"to snapshot {snapshot_name}"
                            )
                            error_message += " [rolled back]"
                        else:
                            _log(f"Rollback to snapshot '{snapshot_name}' failed: {rb.get('error', 'unknown')}", "snapshot_err")
                            logger.error(
                                f"Rollback FAILED for {container['name']}: "
                                f"{rb.get('error', 'unknown')}"
                            )
                    except Exception as e:
                        _log(f"Rollback exception for '{snapshot_name}': {e}", "snapshot_err")
                        logger.error(f"Rollback exception for {container['name']}: {e}")

                self.register_done(container_id)
                _flush_remote_log(result)
                _log("[DONE] Failed — apt-get upgrade ({})".format(container['name']), "footer")
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
                    _log(f"Cleaned up snapshot '{snapshot_name}'", "snapshot")
                    logger.info(f"Cleaned up snapshot {snapshot_name} for {container['name']}")
                except Exception as e:
                    _log(f"Could not clean up snapshot '{snapshot_name}': {e}", "snapshot_err")
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
            self.register_done(container_id)
            _flush_remote_log(result)
            _log("[DONE] Success — {} package(s) updated ({})".format(len(packages_updated), container['name']), "footer")
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

            self.register_done(container_id)
            _log("[DONE] Failed — exception ({})".format(container['name']), "footer")
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
