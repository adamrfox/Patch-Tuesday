"""
Proxmox VE API integration for LXC container discovery and management.

Uses direct HTTP requests to Proxmox's REST API.
API tokens are sent via the PVEAPIToken authorization header (the raw
token secret is not HMAC-signatured — it's a shared-secret).
Password auth uses ticket-based login.
"""

import logging
import time
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Shared session: keep-alive avoids a fresh DNS lookup + TLS handshake per
# request, and connect retries ride out transient name-resolution failures.
_session = requests.Session()
_session.mount('https://', HTTPAdapter(max_retries=Retry(connect=3, read=0, backoff_factor=0.5)))

# (base_url, user) -> (ticket, issued_at). Proxmox tickets are valid for 2h.
_ticket_cache = {}
_TICKET_TTL = 3600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_net_config(net_string):
    """Parse a Proxmox network config string into a dict of key=value pairs."""
    config = {}
    if not net_string:
        return config

    i = 0
    while i < len(net_string):
        eq_pos = net_string.find('=', i)
        if eq_pos == -1:
            break
        key = net_string[i:eq_pos].strip()
        start = eq_pos + 1
        if start < len(net_string) and net_string[start] == '[':
            depth = 1
            end = start + 1
            while end < len(net_string) and depth > 0:
                if net_string[end] == '[':
                    depth += 1
                elif net_string[end] == ']':
                    depth -= 1
                end += 1
            config[key] = net_string[start:end]
            i = end
        else:
            comma_pos = net_string.find(',', start)
            if comma_pos == -1:
                config[key] = net_string[start:].strip()
                break
            config[key] = net_string[start:comma_pos].strip()
            i = comma_pos + 1

    return config


def _extract_ip_from_cidr(cidr):
    """Strip CIDR suffix from an IP address string."""
    return cidr.split('/')[0] if cidr else None


def _extract_interface_ips(interfaces):
    """Extract IPv4 addresses from a /nodes/{node}/lxc/{vmid}/interfaces response.

    Each entry looks like {'name': 'eth0', 'hwaddr': ..., 'inet': '192.168.1.5/24',
    'inet6': ...}; newer Proxmox versions may also include an 'ip-addresses' list
    of {'ip-address': ..., 'ip-address-type': 'inet', 'prefix': ...}.
    Loopback is skipped.
    """
    ips = []
    for iface in interfaces if isinstance(interfaces, list) else []:
        if iface.get('name') == 'lo':
            continue
        candidates = [iface.get('inet')]
        for entry in iface.get('ip-addresses') or []:
            if entry.get('ip-address-type') == 'inet':
                candidates.append(entry.get('ip-address'))
        for cidr in candidates:
            addr = _extract_ip_from_cidr(cidr)
            if addr and not addr.startswith('127.') and addr not in ips:
                ips.append(addr)
    return ips


# ---------------------------------------------------------------------------
# Proxmox auth helpers
# ---------------------------------------------------------------------------


def _parse_token_user(user_string):
    """Parse 'user@realm!keyname' into (full_with_key, key_name) or (user, None).

    Also strips bash-escaped \\! sequences (from histexpand in double quotes).
    """
    cleaned = user_string.replace('\\!', '!').replace('\\\\', '\\')
    if '!' in cleaned:
        parts = cleaned.rsplit('!', 1)
        return f'{parts[0]}!{parts[1]}', parts[1]
    return cleaned, None


def _build_api_token_header(service, username, token_name, token_value):
    """Build the PVEAPIToken Authorization header value.

    Format: {service}APIToken={username}!{token_name}={token_value}
    where service='PVE', username has no ! (just user@realm),
    and token_name is the key name.

    If username already contains !keyname, token_name must be empty.
    """
    # Clean any bash artifacts
    clean_username = username.replace('\\!', '!').replace('\\\\', '\\')
    clean_token_value = (token_value or '').replace('\\!', '!').replace('\\\\', '\\')

    if token_name and '!' not in clean_username:
        return f'{service}APIToken={clean_username}!{token_name}={clean_token_value}'
    elif token_name:
        # ! already in username — ignore extra key_name to avoid double !keyname
        return f'{service}APIToken={clean_username}={clean_token_value}'
    else:
        return f'{service}APIToken={clean_username}={clean_token_value}'


def _get_csrf_cookie(base_url, verify_ssl):
    """Fetch CSRF pre-call cookie from Proxmox /access/token endpoint.

    Returns (csrf_token_string, dict_of_cookies) or raises on failure.
    """
    r = requests.get(
        f'{base_url.rstrip("/")}/api2/json/access/token',
        verify=verify_ssl, timeout=10,
    )
    r.raise_for_status()
    csrf = r.headers.get('PVECSRFToken', '')
    cookies = r.cookies.get_dict()
    return csrf, cookies


def _api_request(method, base_url, user, key_name, token_value, verify_ssl,
                 path, params=None, data=None):
    """Send a Proxmox API request.

    For API tokens: PVEAPIToken header with raw secret.
    For password: ticket-based cookie login via /access/ticket first.
    """
    service = 'PVE'
    url = f'{base_url.rstrip("/")}/api2/json{path}'
    headers = {'Accept': 'application/json'}
    cookies = None

    if token_value:
        # --- API token path (raw secret, no HMAC) ---
        auth_str = _build_api_token_header(service, user, key_name, token_value)
        headers['Authorization'] = auth_str

    elif data and isinstance(data, str):
        # --- Password path: ticket-based login via /access/ticket ---
        # In Proxmox 8+/9.x, /access/token returns 401 without auth.
        # We post directly to /access/ticket and use the ticket as a cookie.
        cache_key = (base_url, user)
        cached = _ticket_cache.get(cache_key)
        if cached and time.time() - cached[1] < _TICKET_TTL:
            pve_ticket = cached[0]
        else:
            login_resp = _session.post(
                f'{base_url.rstrip("/")}/api2/json/access/ticket',
                data={'username': user, 'password': data},
                verify=verify_ssl, timeout=10,
            )
            login_resp.raise_for_status()
            ticket_data = login_resp.json()['data']
            pve_ticket = ticket_data.get('ticket') or ''
            if not pve_ticket:
                raise PermissionError("No ticket returned from /access/ticket")
            _ticket_cache[cache_key] = (pve_ticket, time.time())

        # Use PVEAuthCookie as a cookie for subsequent requests
        cookies = {'PVEAuthCookie': pve_ticket}

    if cookies:
        pass  # cookie-based auth for ticket login
    elif headers.get('Authorization'):
        logger.debug(f"Auth: {headers['Authorization'][:30]}...")

    # Execute the request
    r = _session.request(
        method, url, params=params, data=None,
        headers=headers, cookies=cookies,
        verify=verify_ssl, timeout=15,
    )

    if r.status_code == 401:
        # Drop a possibly-expired cached ticket so the next call logs in again
        _ticket_cache.pop((base_url, user), None)
        raise PermissionError(f"Authentication failed for {path}")
    elif r.status_code >= 400 and r.status_code < 500:
        raw_resp = r.text
        try:
            err_data = r.json()
            # 'data' may exist but be None; check multiple keys for error detail
            detail = err_data.get('errors') or str(err_data)
        except Exception:
            detail = raw_resp

        print(f"[HTTP DEBUG] {method} {url} -> {r.status_code}: {detail!r}")
        raise RuntimeError(f"API error {r.status_code} for {path}: {detail}")

    r.raise_for_status()
    return r.json().get('data', {})


# ---------------------------------------------------------------------------
# Public discovery & import functions
# ---------------------------------------------------------------------------


def discover_proxmox_containers(proxmox_host, proxmox_user=None, proxmox_password=None,
                                proxmox_port=8006, verify_ssl=False,
                                token_name=None, token_value=None):
    """Discover all LXC containers from a Proxmox cluster.

    Supports:
      1. API token: --user 'patch@pam!patchtuesday' + --token-value SECRET
      2. Password:  --password PASSWORD

    Args:
        proxmox_host: Cluster hostname or IP address.
        proxmox_user: Username, may include !keyname e.g. 'root@pam!patchtuesday'
        proxmox_password: Password for password-based auth.
        proxmox_port: API port, default 8006.
        verify_ssl: Verify SSL certificates.
        token_name: API token key name (e.g. 'patchtuesday'). May be redundant
                     if already included in proxmox_user via !keyname.
        token_value: API token secret value (the long random string).

    Returns:
        List of dicts with name, container_id, status, node, primary_ip, etc.
    """
    if not proxmox_host:
        raise ValueError("proxmox_host is required")
    if not proxmox_user:
        raise ValueError("proxmox_user is required (e.g. root@pam or root@pam!patchtuesday)")

    has_token = bool(token_value)
    has_password = bool(proxmox_password)

    if not has_token and not has_password:
        raise ValueError(
            "Authentication required. Provide either:\n"
            "  --token-value SECRET (API token)\n"
            "or\n"
            "  --password PASSWORD (password auth)"
        )

    base_url = f'https://{proxmox_host}:{proxmox_port}'

    # Parse key name from user string if present, otherwise use explicit arg
    full_user_with_key, key_name_from_user = _parse_token_user(proxmox_user)
    effective_key_name = token_name or key_name_from_user

    # Resolve the hostname early — if DNS is broken, fail fast with a clear error.
    # Proxmox clusters often resolve to multiple IPs; when DNS is flaky this saves
    # the user from chasing down random timeout errors later.
    resolved_ips = None
    if isinstance(proxmox_host, str) and not proxmox_host.isdigit():
        import socket as _socket

        try:
            infos = _socket.getaddrinfo(
                proxmox_host, proxmox_port, family=_socket.AF_INET
            )
            resolved_ips = set(info[4][0] for info in infos)
            print(f"[DNS] Resolved '{proxmox_host}' -> {resolved_ips}")
        except Exception:
            print(f"[ERROR] Cannot resolve hostname '{proxmox_host}'. "
                  f"DNS may be misconfigured on this host. "
                  f"You can try providing the cluster's direct IP via the 'host' "
                  f"field in the Discovery form (e.g., 192.168.86.19).")
            return []

    print(f"[DEBUG] Connecting to {base_url} as {full_user_with_key}")
    auth_type = 'API token' if has_token else 'password'
    if has_token:
        print(f"[DEBUG] Token key name: {effective_key_name!r}")
        print(f"[DEBUG] Token value length: {len(token_value)}")
        # Print first 8 chars + last 4 of token for verification
        tv_display = f'{token_value[:8]}...{token_value[-4:]}' if len(token_value) > 12 else '(short)'
        print(f"[DEBUG] Token value preview: {tv_display}")
    print(f"[DEBUG] Auth method: {auth_type}, verify_ssl={verify_ssl}")

    try:
        # Test connection by fetching nodes list
        nodes_data = _api_request(
            'GET', base_url, full_user_with_key, effective_key_name,
            token_value if has_token else None, verify_ssl, '/nodes',
            data=proxmox_password
        )
        node_list = nodes_data if isinstance(nodes_data, list) else []
        print(f"[DEBUG] Auth OK, found {len(node_list)} node(s)")
    except PermissionError as e:
        print(f"[ERROR] Authentication failed — check your credentials:")
        print(f"         User (with key): {full_user_with_key}")
        if has_token:
            print(f"         Token key name: {effective_key_name!r}")
            print(f"         Has token value: True ({len(token_value)} chars)")
            # Show the full auth header that will be sent
            auth_str = _build_api_token_header('PVE', full_user_with_key, effective_key_name, token_value)
            print(f"         Auth header preview: {auth_str[:80]}...")
        else:
            print(f"         Has password: True")
        print(f"         Error: {e}")
        return []
    except Exception as e:
        print(f"[ERROR] Connection failed to {proxmox_host}:{proxmox_port}:")
        print(f"         {type(e).__name__}: {e}")
        return []

    discovered = []

    for node_info in nodes_data:
        node_name = node_info.get('node', 'unknown')
        print(f"[DISCOVER] Checking node: {node_name}")

        # Get LXC container list/status for this node
        try:
            lxc_status = _api_request(
                'GET', base_url, full_user_with_key, effective_key_name,
                token_value if has_token else None, verify_ssl,
                f'/nodes/{node_name}/lxc',
                data=proxmox_password
            )
            status_list = lxc_status if isinstance(lxc_status, list) else []
        except Exception as e:
            print(f"[DISCOVER] ERROR getting LXC list from {node_name}: {e}")
            continue

        print(f"[DISCOVER]   Found {len(status_list)} entries on {node_name}")

        for status_entry in status_list:
            vmid = status_entry.get('vmid')
            name = status_entry.get('name', f'unknown-{vmid}')
            status = status_entry.get('status', 'unknown')
            ctype = status_entry.get('type', 'unknown')
            is_template = status_entry.get('template', 0) == 1

            print(f"[DISCOVER]   -> {name} (vmid={vmid}, type={ctype}, status={status}, template={is_template})")

            if is_template:
                continue
            if ctype != 'lxc':
                continue

            all_ips = []
            mac_addresses = []
            bridges = []
            try:
                config_data = _api_request(
                    'GET', base_url, full_user_with_key, effective_key_name,
                    token_value if has_token else None, verify_ssl,
                    f'/nodes/{node_name}/lxc/{vmid}/config',
                    data=proxmox_password
                )
                config_entries = config_data if isinstance(config_data, dict) else {}

                nets = {}
                for key, val in config_entries.items():
                    if key.startswith('net'):
                        parsed = _parse_net_config(val)
                        nets[key] = parsed

                for idx in range(20):
                    key = f'net{idx}'
                    if key not in nets:
                        break
                    net_parsed = nets[key]
                    ip_raw = net_parsed.get('ip', '')
                    # 'dhcp' / 'manual' are modes, not addresses
                    if ip_raw and ip_raw not in ('dhcp', 'manual'):
                        all_ips.append(_extract_ip_from_cidr(ip_raw))
                    mac = net_parsed.get('hwaddr')
                    if mac:
                        mac_addresses.append(mac)
                    bridge = net_parsed.get('bridge')
                    if bridge and bridge not in bridges:
                        bridges.append(bridge)

                # --- DHCP / guest-reported IPs (running containers only) ---
                if status == 'running':
                    try:
                        interfaces = _api_request(
                            'GET', base_url, full_user_with_key, effective_key_name,
                            token_value if has_token else None, verify_ssl,
                            f'/nodes/{node_name}/lxc/{vmid}/interfaces',
                            data=proxmox_password
                        )
                        for addr in _extract_interface_ips(interfaces):
                            if addr not in all_ips:
                                all_ips.append(addr)
                    except Exception as e:
                        print(f"[DISCOVER]   WARNING getting interfaces for {name}: {e}")
            except Exception as e:
                print(f"[DISCOVER]   ERROR getting config for {name}: {e}")

            discovered.append({
                'name': name,
                'container_id': str(vmid),
                'status': status,
                'node': node_name,
                'primary_ip': all_ips[0] if all_ips else None,
                'all_ips': all_ips,
                'mac_addresses': mac_addresses,
                'bridges': bridges,
            })

    print(f"[DISCOVER] Total discovered: {len(discovered)}")
    return discovered


def import_proxmox_containers(proxmox_containers, ssh_user='root', ssh_port=22, cluster_id=None):
    """Save a list of discovered Proxmox containers into the patch_tuesday database."""
    from .database import save_container, get_container_by_cid, get_container_by_name

    result = {'imported': 0, 'skipped': 0, 'errors': []}

    for c in proxmox_containers:
        existing = get_container_by_cid(c['container_id']) or get_container_by_name(c['name'])
        if existing:
            logger.info(f"Skipping {c['name']} ({c['container_id']}): already imported")
            result['skipped'] += 1
            continue

        container_data = {
            'name': c['name'],
            'container_id': c['container_id'],
            'status': c.get('status', 'unknown'),
            'last_updated': None,
            'updated_needs': False,
            'ssh_host': c.get('primary_ip'),
            'proxmox_node': c.get('node'),
            'ssh_port': ssh_port,
            'ssh_user': ssh_user,
            'snapshot_enabled': True,
            'last_cluster_id': cluster_id,
        }

        try:
            save_container(container_data)
            result['imported'] += 1
            logger.info(f"Imported container: {c['name']} (vmid={c['container_id']})")
        except Exception as e:
            error_msg = f"Failed to import {c['name']} ({c['container_id']}): {e}"
            logger.error(error_msg)
            result['errors'].append(error_msg)

    return result


def discover_from_cluster(cluster):
    """Discover containers from a stored cluster definition.

    Args:
        cluster: dict with keys from proxmox_clusters table (host, user, port, etc.)

    Returns:
        List of dicts (same format as discover_proxmox_containers).
    """
    has_token = bool(cluster.get('token_value'))
    has_password = bool(cluster.get('password'))

    if not has_token and not has_password:
        raise ValueError(f"Cluster '{cluster['name']}' has no credentials configured")

    return discover_proxmox_containers(
        proxmox_host=cluster['host'],
        proxmox_user=cluster['user'],
        proxmox_port=int(cluster.get('port', 8006)),
        verify_ssl=bool(cluster.get('verify_ssl', False)),
        token_name=cluster.get('token_name'),
        token_value=cluster.get('token_value') if has_token else None,
        proxmox_password=cluster.get('password') if has_password else None,
    )


def sync_default_group():
    """Scan all configured clusters and sync the default group membership.

    Returns:
        dict with keys: added (list of new containers), removed (list of deleted names),
                        skipped_not_running (int), errors (list)
    """
    from .database import get_all_proxmox_clusters, delete_container, get_default_schedule

    result = {'added': [], 'removed': set(), 'skipped_not_running': 0, 'errors': []}

    # Only sync if a default schedule is configured and enabled
    default_sched = get_default_schedule()
    if not default_sched or not default_sched.get('enabled'):
        logger.info("No enabled default schedule found — skipping cluster sync")
        return result

    clusters = get_all_proxmox_clusters()
    if not clusters:
        logger.info("No Proxmox clusters configured — skipping cluster sync")
        return result

    # Build a set of all known container_ids across all clusters
    known_containers = {}  # vmid -> discovered info
    for cluster in clusters:
        try:
            containers = discover_from_cluster(cluster)
            for c in containers:
                if c['status'] != 'running':
                    result['skipped_not_running'] += 1
                    continue
                known_containers[c['container_id']] = {
                    'cluster_id': cluster['id'],
                    'cluster_name': cluster['name'],
                    **c,
                }
        except Exception as e:
            logger.error(f"Error discovering from cluster '{cluster['name']}': {e}")
            result['errors'].append(f"{cluster['name']}: {e}")

    # Find containers in the DB that belong to any synced cluster
    from .database import get_all_containers
    all_db_containers = get_all_containers()
    for container in all_db_containers:
        cid = container.get('last_cluster_id')
        if cid is not None and cid in {c['id'] for c in clusters}:
            # This container came from a synced cluster
            actual_vmid = container['container_id']
            if actual_vmid not in known_containers:
                # Container no longer exists on any cluster — hard delete it
                logger.info(f"Removing deleted container: {container['name']} (vmid={actual_vmid})")
                delete_container(container['id'])
                result['removed'].add(container['name'])

    # Add new running containers that aren't in the DB yet
    from .database import get_container_by_cid, get_container_by_name
    for vmid, info in known_containers.items():
        existing = get_container_by_cid(vmid) or get_container_by_name(info['name'])
        if not existing:
            container_data = {
                'name': info['name'],
                'container_id': vmid,
                'status': info.get('status', 'unknown'),
                'last_updated': None,
                'updated_needs': False,
                'ssh_host': info.get('primary_ip'),
                'ssh_port': 22,
                'ssh_user': 'root',
                'snapshot_enabled': True,
                'last_cluster_id': info['cluster_id'],
            }
            try:
                from .database import save_container
                save_container(container_data)
                result['added'].append(info['name'])
                logger.info(f"Auto-added container from cluster '{info['cluster_name']}': {info['name']}")
            except Exception as e:
                logger.error(f"Failed to add container {info['name']}: {e}")
                result['errors'].append(f"{info['name']}: {e}")

    # Convert set to list for JSON serialization
    result['removed'] = list(result['removed'])
    return result


# ---------------------------------------------------------------------------
# Proxmox snapshot helpers (create / rollback / delete)
# ---------------------------------------------------------------------------


def create_lxc_snapshot(node: str, vmid: str, snapshot_name: str) -> dict:
    """Create a Proxmox LXC snapshot.

    Args:
        node: Proxmox node name (e.g. 'pve1').
        vmid: Container VMID (integer or string).
        snapshot_name: Name for the new snapshot.

    Returns:
        dict with 'success' (bool) and 'error' (str, if failed).
    """
    # Discover auth from first matching cluster in DB (heuristic — matches how
    # the rest of this file resolves credentials without an explicit cluster arg).
    try:
        from .database import get_all_proxmox_clusters
        clusters = get_all_proxmox_clusters()
    except Exception:
        clusters = []

    if not clusters:
        return {'success': False, 'error': 'No Proxmox clusters configured'}

    for cluster in clusters:
        try:
            base_url = f'https://{cluster["host"]}:{cluster.get("port", 8006)}'
            user = cluster.get('user', '') or ''
            token_name = cluster.get('token_name', '') or ''
            token_value = cluster.get('token_value', '') or ''
            password = cluster.get('password', '') or ''
            verify_ssl = bool(cluster.get('verify_ssl', False))

            path = f'/nodes/{node}/lxc/{vmid}/snapshot/{snapshot_name}'
            _api_request(
                'PUT', base_url, user, token_name if token_name else (user if '!' in user else None),
                token_value if token_value else None, verify_ssl, path,
                data={'config': 'keep-all=1'} if password else None,
            )
            return {'success': True}
        except Exception as e:
            logger.debug(f"Snapshot create failed on cluster '{cluster.get('name', '?')}': {e}")
            continue

    return {'success': False, 'error': f'Could not connect to any configured cluster for snapshot creation'}


def rollback_lxc_snapshot(node: str, vmid: str, snapshot_name: str) -> dict:
    """Roll back an LXC container to a given snapshot.

    Returns:
        dict with 'success' (bool) and 'error' (str, if failed).
    """
    try:
        from .database import get_all_proxmox_clusters
        clusters = get_all_proxmox_clusters()
    except Exception:
        clusters = []

    if not clusters:
        return {'success': False, 'error': 'No Proxmox clusters configured'}

    for cluster in clusters:
        try:
            base_url = f'https://{cluster["host"]}:{cluster.get("port", 8006)}'
            user = cluster.get('user', '') or ''
            token_name = cluster.get('token_name', '') or ''
            token_value = cluster.get('token_value', '') or ''
            password = cluster.get('password', '') or ''
            verify_ssl = bool(cluster.get('verify_ssl', False))

            path = f'/nodes/{node}/lxc/{vmid}/snapshot/{snapshot_name}/rollback'
            _api_request(
                'PUT', base_url, user, token_name if token_name else (user if '!' in user else None),
                token_value if token_value else None, verify_ssl, path,
                data=None,
            )
            return {'success': True}
        except Exception as e:
            logger.debug(f"Snapshot rollback failed on cluster '{cluster.get('name', '?')}': {e}")
            continue

    return {'success': False, 'error': f'Could not connect to any configured cluster for snapshot rollback'}


def delete_lxc_snapshot(node: str, vmid: str, snapshot_name: str) -> dict:
    """Delete a Proxmox LXC snapshot (cleanup after successful update)."""
    try:
        from .database import get_all_proxmox_clusters
        clusters = get_all_proxmox_clusters()
    except Exception:
        clusters = []

    if not clusters:
        return {'success': False, 'error': 'No Proxmox clusters configured'}

    for cluster in clusters:
        try:
            base_url = f'https://{cluster["host"]}:{cluster.get("port", 8006)}'
            user = cluster.get('user', '') or ''
            token_name = cluster.get('token_name', '') or ''
            token_value = cluster.get('token_value', '') or ''
            password = cluster.get('password', '') or ''
            verify_ssl = bool(cluster.get('verify_ssl', False))

            path = f'/nodes/{node}/lxc/{vmid}/snapshot/{snapshot_name}'
            _api_request(
                'DELETE', base_url, user, token_name if token_name else (user if '!' in user else None),
                token_value if token_value else None, verify_ssl, path,
            )
            return {'success': True}
        except Exception as e:
            logger.debug(f"Snapshot delete failed on cluster '{cluster.get('name', '?')}': {e}")
            continue

    return {'success': False, 'error': f'Could not connect to any configured cluster for snapshot deletion'}


def get_lxc_container_status(node: str, vmid: str) -> dict:
    """Return the current Proxmox status of a container.

    Args:
        node: Proxmox node name (e.g. 'pve01').
        vmid: Container VMID (string or int).

    Returns:
        {'success': True, 'status': 'running'|'stopped'|...} or
        {'success': False, 'error': ...}.
    """
    try:
        from .database import get_all_proxmox_clusters
        clusters = get_all_proxmox_clusters()
    except Exception:
        clusters = []

    if not clusters:
        return {'success': False, 'error': 'No Proxmox clusters configured'}

    for cluster in clusters:
        has_token = bool(cluster.get('token_value'))
        has_password = bool(cluster.get('password'))
        if not has_token and not has_password:
            continue

        proxmox_host = cluster.get('host', '') or ''
        proxmox_port = int(cluster.get('port', 8006))
        user = cluster.get('user', '') or ''
        token_name = cluster.get('token_name', '') or ''
        token_value = cluster.get('token_value', '') or ''
        password = cluster.get('password', '') or ''
        verify_ssl = bool(cluster.get('verify_ssl', False))

        base_url = f'https://{proxmox_host}:{proxmox_port}'
        full_user = user if '!' in user else None
        auth_key = token_name if has_token else None

        try:
            path = f'/nodes/{node}/lxc/{vmid}/status/current'
            resp = _api_request(
                'GET', base_url, full_user, auth_key,
                token_value if has_token else None, verify_ssl, path,
                data=password if not has_token else None,
            )
            if isinstance(resp, dict):
                return {'success': True, 'status': resp.get('status', 'unknown')}
        except Exception as exc:
            logger.debug(f"Status lookup on cluster '{cluster.get('name', '?')}' failed: {exc}")
            continue

    return {'success': False, 'error': 'Could not connect to any configured cluster'}


def _find_node_for_container(container):
    """Heuristic: find the Proxmox node name for a container.

    Strategy: check stored clusters and match by ssh_host IP against discovered IPs.
    Falls back to proxmox_vmid if set but node is not, then falls back to proxmox_node.

    Args:
        container: dict or sqlite3.Row representing a container row.

    Returns:
        (node_name, vmid) tuple or (None, None).
    """
    def _get(obj, key, default=None):
        """Safely get an attribute from dict or sqlite3.Row."""
        if hasattr(obj, 'get'):
            return obj.get(key, default)
        try:
            val = obj[key]
            return val if val is not None else default
        except (KeyError, TypeError):
            return default

    try:
        from .database import get_all_proxmox_clusters
        clusters = get_all_proxmox_clusters()
    except Exception:
        return None, None

    # Try to match by SSH host IP against discovered IPs in known clusters
    ssh_host = _get(container, 'ssh_host') or ''
    for cluster in clusters:
        try:
            discovered = discover_from_cluster(cluster)
            for c in discovered:
                if (str(_get(c, 'container_id')) == str(_get(container, 'container_id')) and
                        _get(c, 'primary_ip') == ssh_host):
                    return _get(c, 'node'), _get(c, 'container_id')
                # Also match by name
                if _get(c, 'name') == _get(container, 'name'):
                    return _get(c, 'node'), _get(c, 'container_id')
        except Exception as e:
            logger.debug(f"Cluster '{_get(cluster, 'name', '?')}' discovery failed: {e}")
            continue

    # Fallback: use stored proxmox_node if available (from import)
    node = _get(container, 'proxmox_node')
    vmid = _get(container, 'container_id')
    if node and vmid:
        return str(node), str(vmid)

    return None, None
