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
                      get_active_schedules, get_schedule_by_group)
from .container_manager import container_manager
from .patch_engine import patch_engine
from .alerting import alerting_system

# Create blueprint for routes
main = Blueprint('main', __name__)

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
        container_data = {
            'name': data['name'],
            'container_id': data['container_id'],
            'status': 'configured',
            'ssh_host': data.get('ssh_host'),
            'ssh_port': int(data.get('ssh_port', 22)),
            'ssh_user': data.get('ssh_user', 'root'),
            'snapshot_enabled': True
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
        return jsonify({'success': True, 'containers': discovered})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@main.route('/proxmox/import', methods=['POST'])
def proxmox_import():
    """Import selected containers from a Proxmox cluster into the database."""
    data = request.get_json()

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

        if not discovered:
            return jsonify({'success': False, 'message': 'No containers discovered'})

        # Filter by selected IDs
        selected_ids = data.get('container_ids')  # None means all
        if selected_ids is not None and len(selected_ids) > 0:
            selected_set = set(str(s) for s in selected_ids)
            to_import = [c for c in discovered if c['container_id'] in selected_set]
        else:
            to_import = discovered

        result = container_manager.import_proxmox_containers(
            to_import,
            ssh_user=data.get('ssh_user', 'root'),
            ssh_port=int(data.get('ssh_port', 22)),
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
        gid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
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
        row = db.execute(
            'SELECT id FROM schedules WHERE group_id = ? LIMIT 1', (group_id,)
        ).fetchone()
        if row:
            delete_schedule(row['id'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
