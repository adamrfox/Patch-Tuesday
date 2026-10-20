"""
Web routes for the patch management system
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from .database import (get_all_containers, get_container_by_id, save_container,
                      delete_container, get_update_history, get_all_schedules,
                      save_schedule, delete_schedule, get_schedule_by_container,
                      get_all_groups, get_group_by_id, save_group, delete_group,
                      get_group_members, get_all_groups_for_container,
                      add_group_member, remove_group_member,
                      get_containers_not_in_group,
                      get_active_schedules, get_schedule_by_group,
                      get_db, get_default_schedule, save_default_schedule, delete_default_schedule,
                      get_default_ssh_user, set_default_ssh_user,
                      get_discovery_config, set_discovery_config,
                      get_all_proxmox_clusters, get_proxmox_cluster_by_id,
                      save_proxmox_cluster, delete_proxmox_cluster)
from .container_manager import container_manager
from .patch_engine import patch_engine
from .alerting import alerting_system
main = Blueprint('main', __name__)


# ---------------------------------------------------------------------------
# Lazy cache for global discovery config (avoids DB hits on every request)
# ---------------------------------------------------------------------------

_discovery_cfg_cache = None


def _get_global_config(key, default=False):
    """Return a cached value from the global discovery config."""
    global _discovery_cfg_cache
    if _discovery_cfg_cache is None:
        _discovery_cfg_cache = get_discovery_config()
    return _discovery_cfg_cache.get(key, default)

@main.route('/')
def index():
    """Main dashboard page"""
    containers = get_all_containers()
    container_count = len(containers)

    # Get some statistics
    containers_with_updates = 0
    successful_updates = 0

    # Placeholder for actual data - in a real implementation,
    # you'd query the database for recent updates

    recent_updates = []
    schedules = get_active_schedules()
    groups = get_all_groups()

    return render_template('index.html',
                         containers=containers,
                         container_count=container_count,
                         containers_with_updates=containers_with_updates,
                         successful_updates=successful_updates,
                         recent_updates=recent_updates,
                         schedules=schedules,
                         groups=groups)

@main.route('/container', methods=['POST'])
def add_container():
    """Add a new container"""
    data = request.get_json()

    try:
        default_user = get_default_ssh_user()
        container_data = {
            'name': data['name'],
            'container_id': data['container_id'],
            'status': 'configured',
            'ssh_host': data.get('ssh_host'),
            'ssh_port': int(data.get('ssh_port', 22)),
            'ssh_user': data.get('ssh_user', default_user),
            'snapshot_enabled': True,
            'ssh_private_key_path': data.get('ssh_private_key_path'),
        }

        save_container(container_data)
        return jsonify({'success': True, 'message': 'Container added successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@main.route('/container/<int:container_id>', methods=['DELETE'])
def remove_container(container_id):
    """Remove a container"""
    try:
        delete_container(container_id)
        return jsonify({'success': True, 'message': 'Container removed successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@main.route('/patch/<int:container_id>', methods=['POST'])
def patch_container(container_id):
    """Apply patches to a container"""
    try:
        result = patch_engine.apply_patches(container_id)

        # Send notification
        container = get_container_by_id(container_id)
        if container:
            alerting_system.send_update_notification(
                container['name'],
                'success' if result['success'] else 'failed',
                packages_updated=result.get('packages_updated'),
                error_message=result.get('error')
            )

        return jsonify({
            'success': result['success'],
            'message': result.get('error', 'Patch operation completed')
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Container live status (is patching? when was last update?)
# ---------------------------------------------------------------------------

@main.route('/api/containers/status')
def containers_status():
    """Return per-container status: last_successful_update and is_patching."""
    from .database import get_db as _get_db
    from datetime import datetime as _dt

    db = _get_db()
    rows = db.execute(
        "SELECT c.id, c.name, c.container_id, c.status, c.last_updated "
        "FROM containers c ORDER BY c.name"
    ).fetchall()
    result = []
    for row in rows:
        cid = row['id']
        latest = db.execute(
            'SELECT MAX(end_time) AS last_succ FROM update_history '
            'WHERE container_id = ? AND status = ?',
            (cid, 'success')
        ).fetchone()
        result.append({
            'id': cid,
            'name': row['name'],
            'container_id': row['container_id'],
            'status': row['status'],
            'last_updated': latest['last_succ'] if latest and latest['last_succ'] else None,
            'is_patching': patch_engine.is_running(cid),
        })
    return jsonify(result)


@main.route('/history/<int:container_id>')
def get_update_history(container_id):
    """Get update history for a container"""
    try:
        updates = get_update_history(container_id)
        # Convert to list of dictionaries
        result = []
        for update in updates:
            result.append({
                'id': update['id'],
                'container_id': update['container_id'],
                'status': update['status'],
                'start_time': update['start_time'],
                'end_time': update['end_time'],
                'packages_updated': update['packages_updated'],
                'output': update['output'],
                'error_message': update['error_message']
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)})

@main.route('/schedule', methods=['POST'])
def create_schedule():
    """Create a new schedule"""
    try:
        data = request.get_json()

        schedule_data = {
            'container_id': int(data['container_id']),
            'schedule_type': data['schedule_type'],
            'time_of_day': data['time_of_day'],
            'day_of_week': int(data.get('day_of_week', 1)),
            'enabled': True,
            'maintenance_window_start': data.get('maintenance_window_start'),
            'maintenance_window_end': data.get('maintenance_window_end')
        }

        save_schedule(schedule_data)
        return jsonify({'success': True, 'message': 'Schedule created successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@main.route('/schedule/<int:schedule_id>/toggle', methods=['POST'])
def toggle_schedule(schedule_id):
    """Toggle schedule enabled/disabled"""
    try:
        # In a real implementation, we'd fetch the schedule and toggle its status
        # For now, just return success
        return jsonify({'success': True, 'message': 'Schedule toggled successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@main.route('/schedule/<int:schedule_id>', methods=['DELETE'])
def delete_schedule_route(schedule_id):
    """Delete a schedule"""
    try:
        delete_schedule(schedule_id)
        return jsonify({'success': True, 'message': 'Schedule deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@main.route('/proxmox/discover', methods=['POST'])
def proxmox_discover():
    """Discover containers from a Proxmox cluster and return as JSON."""
    data = request.get_json()

    print(f"[ROUTE] /proxmox/discover called with: host={data.get('host')}, user={data.get('user')}, token_value_len={len(data.get('token_value',''))}")

    try:
        discovered = container_manager.discover_containers_from_proxmox(
            proxmox_host=data['host'],
            proxmox_user=data.get('user'),
            proxmox_password=data.get('password'),
            proxmox_port=int(data.get('port', 8006)),
            verify_ssl=bool(data.get('verify_ssl', True)),
            token_name=data.get('token_name'),
            token_value=data.get('token_value'),
        )

        # Apply global 172.x filter setting
        ignore_172 = _get_global_config('ignore_172_networks', False)
        filtered_count = 0
        if ignore_172:
            before = len(discovered)
            for c in discovered:
                # Also clean secondary IPs from the all_ips list (displayed in the results table)
                if c.get('all_ips'):
                    c['all_ips'] = [ip for ip in c['all_ips'] if not ip.startswith('172.')]
            discovered = [c for c in discovered if not (c.get('primary_ip') or '').startswith('172.')]
            filtered_count = before - len(discovered)

        return jsonify({'success': True, 'containers': discovered, 'filtered_172': filtered_count})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@main.route('/proxmox/import', methods=['POST'])
def proxmox_import():
    """Import selected containers from a Proxmox cluster into the database.

    Accepts inline auth params OR a stored cluster_id for credential lookup.
    """
    data = request.get_json()

    try:
        # If cluster_id is provided, discover from that stored cluster
        cluster_id = data.get('cluster_id')
        if cluster_id:
            cluster = get_proxmox_cluster_by_id(cluster_id)
            if not cluster:
                return jsonify({'success': False, 'message': f'Cluster {cluster_id} not found'}), 404
            discovered = container_manager.discover_from_cluster(cluster)
        else:
            discovered = container_manager.discover_containers_from_proxmox(
                proxmox_host=data['host'],
                proxmox_user=data.get('user'),
                proxmox_password=data.get('password'),
                proxmox_port=int(data.get('port', 8006)),
                verify_ssl=bool(data.get('verify_ssl', True)),
                token_name=data.get('token_name'),
                token_value=data.get('token_value'),
            )

        if not discovered:
            return jsonify({'success': False, 'message': 'No containers discovered'})

        # Filter by selected IDs
        selected_ids = data.get('container_ids')  # None means all
        if selected_ids is not None and len(selected_ids) > 0:
            selected_set = set(str(s) for s in selected_ids)
            to_import = [c for c in discovered if c['container_id'] in selected_set]
        else:
            to_import = discovered

        default_user = get_default_ssh_user()
        result = container_manager.import_proxmox_containers(
            to_import,
            ssh_user=data.get('ssh_user', default_user),
            ssh_port=int(data.get('ssh_port', 22)),
            cluster_id=cluster_id,
        )

        return jsonify({
            'success': True,
            'imported_count': result['imported'],
            'skipped_count': result['skipped'],
            'errors': result['errors'],
        })
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Group management routes
# ---------------------------------------------------------------------------

@main.route('/groups', methods=['GET'])
def list_groups():
    """Return all groups."""
    return jsonify({'success': True, 'groups': get_all_groups()})


@main.route('/groups', methods=['POST'])
def create_group():
    """Create a new group."""
    data = request.get_json()
    try:
        name = data['name'].strip()
        if not name:
            return jsonify({'success': False, 'message': 'Group name is required'}), 400
        save_group({'name': name, 'description': data.get('description', '')})
        db = get_db()
        gid = db.execute('SELECT id FROM groups ORDER BY id DESC LIMIT 1').fetchone()[0]
        group = get_group_by_id(gid)
        return jsonify({'success': True, 'group': group})
    except Exception as e:
        if 'UNIQUE constraint failed' in str(e):
            return jsonify({'success': False, 'message': f'Group "{name}" already exists'}), 409
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/groups/<int:group_id>', methods=['DELETE'])
def delete_group_route(group_id):
    """Delete a group."""
    try:
        delete_group(group_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# Group members

@main.route('/groups/<int:group_id>/members', methods=['GET'])
def group_members_list(group_id):
    """Return containers belonging to a group."""
    if not get_group_by_id(group_id):
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    return jsonify({'success': True, 'members': get_group_members(group_id)})


@main.route('/groups/<int:group_id>/members', methods=['POST'])
def group_add_member(group_id):
    """Assign a container to a group."""
    data = request.get_json()
    try:
        add_group_member(group_id, int(data['container_id']))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@main.route('/groups/<int:group_id>/containers/available')
def group_available_containers(group_id):
    """Return containers not yet in this group."""
    if not get_group_by_id(group_id):
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    return jsonify({'success': True, 'containers': get_containers_not_in_group(group_id)})


@main.route('/groups/<int:group_id>/members/batch', methods=['POST'])
def group_add_multiple_members(group_id):
    """Add multiple containers to a group in one call."""
    if not get_group_by_id(group_id):
        return jsonify({'success': False, 'message': 'Group not found'}), 404
    data = request.get_json()
    container_ids = data.get('container_ids', [])
    added = []
    for cid in container_ids:
        try:
            add_group_member(group_id, int(cid))
            added.append(int(cid))
        except Exception:
            pass
    return jsonify({'success': True, 'added': added})


@main.route('/groups/<int:group_id>/members/<int:container_id>', methods=['DELETE'])
def group_remove_member(group_id, container_id):
    """Remove a container from a group."""
    try:
        remove_group_member(group_id, container_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@main.route('/container/<int:container_id>/groups', methods=['GET'])
def container_groups(container_id):
    """Return all groups that contain a given container."""
    return jsonify({'success': True, 'groups': get_all_groups_for_container(container_id)})


# Group schedule routes

@main.route('/groups/<int:group_id>/schedule', methods=['GET'])
def get_group_schedule(group_id):
    """Return the schedule assigned to a group, or null."""
    sched = get_schedule_by_group(group_id)
    if sched is None:
        return jsonify({'success': True, 'schedule': None})
    return jsonify({'success': True, 'schedule': sched})


@main.route('/groups/<int:group_id>/schedule', methods=['POST'])
def save_group_schedule(group_id):
    """Create or update the schedule for a group."""
    data = request.get_json()
    try:
        db = get_db()
        existing = db.execute(
            'SELECT id FROM schedules WHERE group_id = ? AND enabled = 1 LIMIT 1', (group_id,)
        ).fetchone()

        if existing:
            schedule_data = {
                'id': existing['id'],
                'schedule_type': data.get('schedule_type', 'daily'),
                'time_of_day': data.get('time_of_day'),
                'day_of_week': int(data.get('day_of_week', 1)),
                'enabled': True,
                'maintenance_window_start': data.get('maintenance_window_start'),
                'maintenance_window_end': data.get('maintenance_window_end'),
            }
            save_schedule(schedule_data)
        else:
            schedule_data = {
                'group_id': group_id,
                'schedule_type': data.get('schedule_type', 'daily'),
                'time_of_day': data.get('time_of_day'),
                'day_of_week': int(data.get('day_of_week', 1)),
                'enabled': True,
                'maintenance_window_start': data.get('maintenance_window_start'),
                'maintenance_window_end': data.get('maintenance_window_end'),
            }
            save_schedule(schedule_data)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/schedule/group/<int:group_id>', methods=['DELETE'])
def delete_group_schedule(group_id):
    """Delete the schedule assigned to a group."""
    try:
        db = get_db()
        row = db.execute(
            'SELECT id FROM schedules WHERE group_id = ? LIMIT 1', (group_id,)
        ).fetchone()
        if row:
            delete_schedule(row['id'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Default schedule API
# ---------------------------------------------------------------------------

@main.route('/default-schedule', methods=['GET'])
def get_default_schedule_route():
    """Return the current default schedule config."""
    cfg = get_default_schedule()
    return jsonify({'success': True, 'schedule': cfg or {}})


@main.route('/default-schedule', methods=['POST'])
def save_default_schedule_route():
    """Save a new default schedule configuration."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    try:
        save_default_schedule(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/default-schedule', methods=['DELETE'])
def delete_default_schedule_route():
    """Remove the default schedule configuration."""
    try:
        delete_default_schedule()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Default SSH user API
# ---------------------------------------------------------------------------

@main.route('/default-ssh-user', methods=['GET'])
def get_default_ssh_user_route():
    """Return the current default SSH user."""
    val = get_default_ssh_user()
    return jsonify({'success': True, 'ssh_user': val})


@main.route('/default-ssh-user', methods=['POST'])
def save_default_ssh_user_route():
    """Save a new default SSH user."""
    data = request.get_json()
    if not data or 'ssh_user' not in data:
        return jsonify({'success': False, 'message': 'ssh_user is required'}), 400
    try:
        set_default_ssh_user(data['ssh_user'].strip())
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Discovery settings API
# ---------------------------------------------------------------------------

@main.route('/discovery-settings', methods=['GET'])
def get_discovery_settings_route():
    """Return global discovery settings."""
    return jsonify({'success': True, 'settings': get_discovery_config()})


@main.route('/discovery-settings', methods=['POST'])
def save_discovery_settings_route():
    """Save global discovery settings."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    try:
        set_discovery_config(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Proxmox cluster management routes
# ---------------------------------------------------------------------------

@main.route('/proxmox/clusters', methods=['GET'])
def list_proxmox_clusters():
    """Return all configured Proxmox clusters."""
    clusters = get_all_proxmox_clusters()
    # Mask credentials in the response
    for c in clusters:
        if c.get('token_value'):
            c['token_value'] = '***' + c['token_value'][-4:]
        if c.get('password'):
            c['password'] = '***'
    return jsonify({'success': True, 'clusters': clusters})


@main.route('/proxmox/clusters', methods=['POST'])
def save_proxmox_cluster_route():
    """Create or update a Proxmox cluster definition."""
    data = request.get_json()
    try:
        name = data['name'].strip()
        host = data['host'].strip()
        if not name or not host:
            return jsonify({'success': False, 'message': 'name and host are required'}), 400
        save_proxmox_cluster({
            'id': data.get('id'),
            'name': name,
            'host': host,
            'port': int(data.get('port', 8006)),
            'user': data.get('user', ''),
            'token_name': data.get('token_name', ''),
            'token_value': data.get('token_value', ''),
            'password': data.get('password', ''),
            'verify_ssl': data.get('verify_ssl', False),
        })
        # Get the saved cluster (mask credentials)
        cluster_id = data.get('id')
        if cluster_id:
            cluster = get_proxmox_cluster_by_id(cluster_id)
        else:
            cluster = get_all_proxmox_clusters()[-1]  # last inserted
        if cluster and cluster.get('token_value'):
            cluster['token_value'] = '***' + cluster['token_value'][-4:]
        if cluster and cluster.get('password'):
            cluster['password'] = '***'
        return jsonify({'success': True, 'cluster': cluster})
    except Exception as e:
        if 'UNIQUE constraint failed' in str(e):
            return jsonify({'success': False, 'message': f'Cluster "{name}" already exists'}), 409
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/proxmox/clusters/<int:cluster_id>', methods=['DELETE'])
def delete_proxmox_cluster_route(cluster_id):
    """Delete a Proxmox cluster definition."""
    try:
        delete_proxmox_cluster(cluster_id)
        return jsonify({'success': True, 'message': 'Cluster deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Discover from all clusters
# ---------------------------------------------------------------------------

@main.route('/proxmox/discover-all', methods=['POST'])
def discover_all_clusters():
    """Discover containers from all configured Proxmox clusters."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        from .proxmox_api import discover_from_cluster
        from .database import get_all_proxmox_clusters

        clusters = get_all_proxmox_clusters()
        if not clusters:
            return jsonify({'success': False, 'message': 'No Proxmox clusters configured'})

        all_containers = []
        for cluster in clusters:
            try:
                containers = discover_from_cluster(cluster)
                for c in containers:
                    c['cluster_id'] = cluster['id']
                    c['cluster_name'] = cluster['name']
                all_containers.extend(containers)
                logger.info(f"Discovered {len(containers)} container(s) from cluster '{cluster['name']}'")
            except Exception as e:
                logger.error(f"Error discovering from cluster '{cluster['name']}': {e}")

        # Filter out 172.x IPs if global setting is enabled
        ignore_172 = _get_global_config('ignore_172_networks', False)
        filtered_count = 0
        if ignore_172:
            before = len(all_containers)
            for c in all_containers:
                # Also clean secondary IPs from the all_ips list (displayed in the results table)
                if c.get('all_ips'):
                    c['all_ips'] = [ip for ip in c['all_ips'] if not ip.startswith('172.')]
            all_containers = [c for c in all_containers if not (c.get('primary_ip') or '').startswith('172.')]
            filtered_count = before - len(all_containers)

        return jsonify({'success': True, 'containers': all_containers, 'clusters_checked': len(clusters), 'filtered_172': filtered_count})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ---------------------------------------------------------------------------
# Sync default group with cluster state
# ---------------------------------------------------------------------------

@main.route('/proxmox/sync-default-group', methods=['POST'])
def sync_default_group_route():
    """Sync the default group membership with current cluster container state.

    - Adds new running containers to the default group
    - Removes containers that no longer exist on any configured cluster
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        from .proxmox_api import sync_default_group
        result = sync_default_group()
        return jsonify({
            'success': True,
            'added': result['added'],
            'removed': result['removed'],
            'skipped_not_running': result['skipped_not_running'],
            'errors': result['errors'],
        })
    except Exception as e:
        logger.error(f"Error syncing default group: {e}")
        return jsonify({'success': False, 'message': str(e)})
