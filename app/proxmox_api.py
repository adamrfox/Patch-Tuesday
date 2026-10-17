"""
Proxmox VE API integration for LXC container discovery and management.
"""
import logging
from proxmoxer import ProxmoxAPI

logger = logging.getLogger(__name__)


def _parse_net_config(net_string):
    """Parse a Proxmox network config string into a dict of key=value pairs.

    Example input:
        'name=eth0,ip=192.168.1.100/24,gw=192.168.1.1,bridge=vmbr0,hwaddr=AABBCCDDEEFF'

    Returns dict of parsed key-value pairs.
    """
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
            # Handle nested brackets (e.g. ip=[10.0.0.1/24])
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
    """Strip CIDR suffix from an IP address string (e.g. '10.0.0.5/24' -> '10.0.0.5')."""
    return cidr.split('/')[0] if cidr else None


def discover_proxmox_containers(proxmox_host, proxmox_user=None, proxmox_password=None,
                                proxmox_port=8006, verify_ssl=False,
                                token_name=None, token_value=None):
    """Discover all LXC containers from a Proxmox cluster.

    Args:
        proxmox_host: Cluster hostname or IP address.
        proxmox_user: Username (optional). May use API token format 'user@realm!keyname'.
        proxmox_password: Password for password-based auth (fallback if no token).
        proxmox_port: API port, default 8006.
        verify_ssl: Whether to verify SSL certificates.
        token_name: API token key name (e.g. 'my-token').
        token_value: API token value (the secret part).

    Returns:
        List of dicts with keys:
            name, container_id, status, node, primary_ip, all_ips,
            mac_addresses, bridges.
    """
    if not proxmox_user:
        raise ValueError("proxmox_user is required")

    # Determine auth method
    kwargs = {
        'base_url': f'https://{proxmox_host}:{proxmox_port}',
        'verify_ssl': verify_ssl,
    }

    if token_name and token_value:
        kwargs['user'] = proxmox_user
        kwargs['token_name'] = token_name
        kwargs['token_value'] = token_value
    elif proxmox_password:
        kwargs['user'] = proxmox_user
        kwargs['password'] = proxmox_password
    else:
        raise ValueError("Either (token_name + token_value) or password is required")

    logger.info(f"Connecting to Proxmox at {proxmox_host}:{proxmox_port} as {proxmox_user}")

    try:
        proxmox = ProxmoxAPI(**kwargs)
        # Quick test that auth works
        proxmox.nodes.get()
    except Exception as e:
        logger.error(f"Failed to connect to Proxmox: {e}")
        return []

    discovered = []

    for node_info in proxmox.nodes.get():
        node_name = node_info['node']

        # Get runtime status list for this node
        try:
            status_list = proxmox.nodes(node_name).lxc.get()
            status_map = {s['vmid']: s.get('status', 'unknown') for s in status_list}
        except Exception as e:
            logger.warning(f"Could not get runtime status from node {node_name}: {e}")
            status_map = {}

        # Get config list (net, IP, etc.)
        try:
            containers = proxmox.nodes(node_name).lxc.config.get()
        except Exception as e:
            logger.warning(f"Could not get config from node {node_name}: {e}")
            continue

        for container in containers:
            vmid = container.get('vmid')
            name = container.get('name', f'unknown-{vmid}')

            # Skip templates
            if container.get('template', 0) == 1:
                continue

            status = status_map.get(vmid, 'unknown')

            # Parse network interfaces (net0, net1, ...)
            all_ips = []
            mac_addresses = []
            bridges = []

            idx = 0
            while True:
                key = f'net{idx}'
                net_val = container.get(key)
                if not net_val:
                    break

                net_config = _parse_net_config(net_val)
                ip_raw = net_config.get('ip', '')
                if ip_raw:
                    all_ips.append(_extract_ip_from_cidr(ip_raw))
                mac = net_config.get('hwaddr')
                if mac:
                    mac_addresses.append(mac)
                bridge = net_config.get('bridge')
                if bridge and bridge not in bridges:
                    bridges.append(bridge)

                idx += 1

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

    logger.info(f"Discovered {len(discovered)} containers from Proxmox cluster ({proxmox_host})")
    return discovered


def import_proxmox_containers(proxmox_containers, ssh_user='root', ssh_port=22):
    """Save a list of discovered Proxmox containers into the patch_manager database.

    Args:
        proxmox_containers: List of dicts from discover_proxmox_containers().
        ssh_user: Default SSH user for imported containers.
        ssh_port: Default SSH port for imported containers.

    Returns:
        dict with 'imported' (count), 'skipped' (count, due to unique constraint),
        'errors' (list of error messages).
    """
    from .database import save_container, get_container_by_cid, get_container_by_name

    result = {'imported': 0, 'skipped': 0, 'errors': []}

    for c in proxmox_containers:
        # Skip if already exists
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
            'ssh_port': ssh_port,
            'ssh_user': ssh_user,
            'snapshot_enabled': True,
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
