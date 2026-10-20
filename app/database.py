"""
Database schema and operations for patch management system
"""
import sqlite3
import os
from flask import g, current_app

def get_db():
    """Get database connection"""
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE_PATH'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    """Close database connection"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db(app):
    """Initialize the database with required tables"""
    with app.app_context():
        db = get_db()

        # Create containers table
        db.execute('''
            CREATE TABLE IF NOT EXISTS containers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                container_id TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'unknown',
                last_updated TIMESTAMP,
                updated_needs BOOLEAN DEFAULT 0,
                ssh_host TEXT,
                ssh_port INTEGER DEFAULT 22,
                ssh_user TEXT DEFAULT 'root',
                snapshot_enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create update_history table
        db.execute('''
            CREATE TABLE IF NOT EXISTS update_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                container_id INTEGER,
                status TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                packages_updated TEXT,
                output TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (container_id) REFERENCES containers (id)
            )
        ''')

        # Create schedules table
        db.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                container_id INTEGER,
                schedule_type TEXT DEFAULT 'daily',  -- daily, weekly, custom
                time_of_day TEXT,  -- HH:MM format
                day_of_week INTEGER DEFAULT 1,  -- 0=Sunday, 6=Saturday
                enabled BOOLEAN DEFAULT 1,
                maintenance_window_start TEXT,
                maintenance_window_end TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (container_id) REFERENCES containers (id)
            )
        ''')

        # Create configuration table
        db.execute('''
            CREATE TABLE IF NOT EXISTS configuration (
                id INTEGER PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                description TEXT
            )
        ''')

        # Insert default configurations
        default_configs = [
            ('ntfy_url', '', 'ntfy notification URL'),
            ('smtp_host', '', 'SMTP server host'),
            ('smtp_port', '587', 'SMTP server port'),
            ('smtp_user', '', 'SMTP username'),
            ('smtp_password', '', 'SMTP password'),
            ('log_retention_days', '30', 'Number of days to retain logs'),
            ('default_ssh_user', 'root', 'Default SSH user for new containers'),
        ]

        for key, value, description in default_configs:
            db.execute('''
                INSERT OR IGNORE INTO configuration (key, value, description)
                VALUES (?, ?, ?)
            ''', (key, value, description))

        # Ensure groups schema exists (idempotent migration)
        _ensure_groups_schema(db)

        # Ensure proxmox_clusters table and containers.last_cluster_id exist
        _ensure_proxmox_schema(db)

        db.commit()

def get_container_by_id(container_id):
    """Get container by ID.  Always returns a plain dict (not sqlite3.Row)."""
    db = get_db()
    row = db.execute('SELECT * FROM containers WHERE id = ?', (container_id,)).fetchone()
    return dict(row) if row else None

def get_all_containers():
    """Get all containers.  Returns list of dicts."""
    db = get_db()
    return [dict(r) for r in db.execute('SELECT * FROM containers ORDER BY name').fetchall()]

def get_container_by_name(name):
    """Get container by name.  Always returns a plain dict (not sqlite3.Row)."""
    db = get_db()
    row = db.execute('SELECT * FROM containers WHERE name = ?', (name,)).fetchone()
    return dict(row) if row else None

def get_container_by_cid(container_id):
    """Get container by container ID.  Always returns a plain dict."""
    db = get_db()
    row = db.execute('SELECT * FROM containers WHERE container_id = ?', (container_id,)).fetchone()
    return dict(row) if row else None

def save_container(container_data):
    """Save or update a container"""
    db = get_db()

    private_key = container_data.get('ssh_private_key_path')

    if 'id' in container_data and container_data['id']:
        # Update existing
        cluster_id = container_data.get('last_cluster_id')
        db.execute('''
            UPDATE containers
            SET name=?, container_id=?, status=?, last_updated=?, updated_needs=?,
                ssh_host=?, ssh_port=?, ssh_user=?, snapshot_enabled=?,
                last_cluster_id=?, ssh_private_key_path=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            container_data['name'], container_data['container_id'], container_data['status'],
            container_data.get('last_updated'), container_data.get('updated_needs'),
            container_data.get('ssh_host'), container_data.get('ssh_port'),
            container_data.get('ssh_user'), container_data.get('snapshot_enabled'),
            cluster_id, private_key,
            container_data['id']
        ))
    else:
        # Insert new
        db.execute('''
            INSERT INTO containers (name, container_id, status, last_updated, updated_needs,
                                   ssh_host, ssh_port, ssh_user, snapshot_enabled, last_cluster_id,
                                   ssh_private_key_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            container_data['name'], container_data['container_id'], container_data['status'],
            container_data.get('last_updated'), container_data.get('updated_needs'),
            container_data.get('ssh_host'), container_data.get('ssh_port'),
            container_data.get('ssh_user'), container_data.get('snapshot_enabled'),
            container_data.get('last_cluster_id'), private_key
        ))

    db.commit()

def delete_container(container_id):
    """Delete a container"""
    db = get_db()
    db.execute('DELETE FROM containers WHERE id = ?', (container_id,))
    db.commit()

def save_update_history(update_data):
    """Save update history"""
    db = get_db()
    db.execute('''
        INSERT INTO update_history (container_id, status, start_time, end_time, packages_updated, output, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        update_data['container_id'], update_data['status'], update_data['start_time'],
        update_data['end_time'], update_data['packages_updated'], update_data['output'],
        update_data['error_message']
    ))
    db.commit()

def get_update_history(container_id, limit=50):
    """Get update history for a container"""
    db = get_db()
    return db.execute('''
        SELECT * FROM update_history
        WHERE container_id = ?
        ORDER BY start_time DESC
        LIMIT ?
    ''', (container_id, limit)).fetchall()

def get_configuration(key):
    """Get configuration value"""
    db = get_db()
    result = db.execute('SELECT value FROM configuration WHERE key = ?', (key,)).fetchone()
    return result['value'] if result else None

def set_configuration(key, value):
    """Set configuration value"""
    db = get_db()
    db.execute('''
        INSERT OR REPLACE INTO configuration (key, value) VALUES (?, ?)
    ''', (key, value))
    db.commit()

def get_all_schedules():
    """Get all schedules"""
    db = get_db()
    return db.execute('SELECT * FROM schedules ORDER BY id').fetchall()

def save_schedule(schedule_data):
    """Save or update a schedule"""
    db = get_db()

    if 'id' in schedule_data and schedule_data['id']:
        # Update existing
        db.execute('''
            UPDATE schedules
            SET schedule_type=?, time_of_day=?, day_of_week=?, enabled=?,
                maintenance_window_start=?, maintenance_window_end=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            schedule_data['schedule_type'], schedule_data['time_of_day'],
            schedule_data['day_of_week'], schedule_data['enabled'],
            schedule_data.get('maintenance_window_start'),
            schedule_data.get('maintenance_window_end'),
            schedule_data['id']
        ))
    else:
        # Insert new
        db.execute('''
            INSERT INTO schedules (container_id, schedule_type, time_of_day, day_of_week, enabled,
                                 maintenance_window_start, maintenance_window_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            schedule_data['container_id'], schedule_data['schedule_type'],
            schedule_data['time_of_day'], schedule_data['day_of_week'],
            schedule_data['enabled'], schedule_data.get('maintenance_window_start'),
            schedule_data.get('maintenance_window_end')
        ))

    db.commit()

def get_schedule_by_container(container_id):
    """Get schedule for a container"""
    db = get_db()
    return db.execute('SELECT * FROM schedules WHERE container_id = ?', (container_id,)).fetchone()

def delete_schedule(schedule_id):
    """Delete a schedule"""
    db = get_db()
    db.execute('DELETE FROM schedules WHERE id = ?', (schedule_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Groups migration — run at startup
# ---------------------------------------------------------------------------

def _ensure_groups_schema(db):
    """Ensure groups / group_members tables and schedules.group_id column exist."""
    # 1) Check if group_id already exists on schedules
    cols = {row[1] for row in db.execute('PRAGMA table_info(schedules)').fetchall()}
    if 'group_id' not in cols:
        db.execute('ALTER TABLE schedules ADD COLUMN group_id INTEGER')

    # 2) Create groups table (idempotent)
    db.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 3) Create group_members junction table
    db.execute('''
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL,
            container_id INTEGER NOT NULL,
            PRIMARY KEY (group_id, container_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (container_id) REFERENCES containers(id) ON DELETE CASCADE
        )
    ''')


# ---------------------------------------------------------------------------
# Proxmox migration — run at startup
# ---------------------------------------------------------------------------

def _ensure_proxmox_schema(db):
    """Ensure proxmox_clusters table exists and containers has last_cluster_id."""
    # 1) Create proxmox_clusters table (idempotent)
    db.execute('''
        CREATE TABLE IF NOT EXISTS proxmox_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            host TEXT NOT NULL,
            port INTEGER DEFAULT 8006,
            user TEXT NOT NULL,
            token_name TEXT DEFAULT '',
            token_value TEXT DEFAULT '',
            password TEXT DEFAULT '',
            verify_ssl BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2) Add last_cluster_id column to containers if missing
    cols = {row[1] for row in db.execute('PRAGMA table_info(containers)').fetchall()}
    if 'last_cluster_id' not in cols:
        db.execute('ALTER TABLE containers ADD COLUMN last_cluster_id INTEGER DEFAULT NULL')
        db.execute('''
            CREATE INDEX IF NOT EXISTS idx_containers_last_cluster
            ON containers(last_cluster_id)
        ''')

    # 3) Add proxmox_node column (needed for snapshot/rollback lookups)
    cols = {row[1] for row in db.execute('PRAGMA table_info(containers)').fetchall()}
    if 'proxmox_node' not in cols:
        db.execute('ALTER TABLE containers ADD COLUMN proxmox_node TEXT DEFAULT NULL')

    # 4) Add ssh_private_key_path column (needed for SSH auth)
    cols = {row[1] for row in db.execute('PRAGMA table_info(containers)').fetchall()}
    if 'ssh_private_key_path' not in cols:
        db.execute('ALTER TABLE containers ADD COLUMN ssh_private_key_path TEXT DEFAULT NULL')

    # Seed existing containers with the host's default SSH key (midway user)
    import os
    default_key = f'{os.path.expanduser("~")}/.ssh/id_ed25519'
    if os.path.exists(default_key):
        db.execute(
            'UPDATE containers SET ssh_private_key_path=? WHERE ssh_private_key_path IS NULL',
            (default_key,)
        )


# ---------------------------------------------------------------------------
# Proxmox cluster CRUD
# ---------------------------------------------------------------------------

def get_all_proxmox_clusters():
    """Return all configured Proxmox clusters."""
    db = get_db()
    rows = db.execute(
        'SELECT * FROM proxmox_clusters ORDER BY name'
    ).fetchall()
    return [dict(r) for r in rows]


def get_proxmox_cluster_by_id(cluster_id):
    """Return a single Proxmox cluster dict, or None."""
    db = get_db()
    row = db.execute(
        'SELECT * FROM proxmox_clusters WHERE id = ?', (cluster_id,)
    ).fetchone()
    return dict(row) if row else None


def save_proxmox_cluster(cluster_data):
    """Insert or update a Proxmox cluster.  cluster_data must contain 'name' and 'host'.
    For updates also include 'id'.

    Passwords/tokens are stored as plain text (user confirmed this is acceptable).
    """
    db = get_db()
    if 'id' in cluster_data and cluster_data['id']:
        db.execute('''
            UPDATE proxmox_clusters
            SET name=?, host=?, port=?, user=?, token_name=?, token_value=?,
                password=?, verify_ssl=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            cluster_data['name'],
            cluster_data['host'],
            int(cluster_data.get('port', 8006)),
            cluster_data['user'],
            cluster_data.get('token_name', ''),
            cluster_data.get('token_value', ''),
            cluster_data.get('password', ''),
            bool(cluster_data.get('verify_ssl', False)),
            cluster_data['id'],
        ))
    else:
        db.execute('''
            INSERT INTO proxmox_clusters
                (name, host, port, user, token_name, token_value, password, verify_ssl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            cluster_data['name'],
            cluster_data['host'],
            int(cluster_data.get('port', 8006)),
            cluster_data['user'],
            cluster_data.get('token_name', ''),
            cluster_data.get('token_value', ''),
            cluster_data.get('password', ''),
            bool(cluster_data.get('verify_ssl', False)),
        ))
    db.commit()


def delete_proxmox_cluster(cluster_id):
    """Delete a Proxmox cluster definition."""
    db = get_db()
    db.execute('DELETE FROM proxmox_clusters WHERE id = ?', (cluster_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def get_all_groups():
    """Return all groups with member counts."""
    db = get_db()
    rows = db.execute('''
        SELECT g.id, g.name, g.description, g.created_at, g.updated_at,
               COUNT(gm.container_id) AS member_count
        FROM groups g
        LEFT JOIN group_members gm ON gm.group_id = g.id
        GROUP BY g.id
        ORDER BY g.name
    ''').fetchall()
    return [dict(r) for r in rows]


def get_group_by_id(group_id):
    """Return a single group dict, or None."""
    db = get_db()
    row = db.execute('SELECT * FROM groups WHERE id = ?', (group_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d['member_count'] = db.execute(
        'SELECT COUNT(*) AS cnt FROM group_members WHERE group_id = ?', (group_id,)
    ).fetchone()['cnt']
    return d


def save_group(group_data):
    """Insert or update a group.  group_data must contain at least 'name'.
    For updates also include 'id'."""
    db = get_db()
    if 'id' in group_data and group_data['id']:
        db.execute('''
            UPDATE groups SET name=?, description=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (group_data['name'], group_data.get('description', ''), group_data['id']))
    else:
        db.execute(
            'INSERT INTO groups (name, description) VALUES (?, ?)',
            (group_data['name'], group_data.get('description', ''))
        )
    db.commit()


def delete_group(group_id):
    """Delete a group (FK cascade removes group_members rows). Also remove orphaned schedules."""
    db = get_db()
    # Remove any schedules assigned to this group
    db.execute('DELETE FROM schedules WHERE group_id = ?', (group_id,))
    db.execute('DELETE FROM groups WHERE id = ?', (group_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Group ↔ Container membership
# ---------------------------------------------------------------------------

def get_group_members(group_id):
    """Return list of container dicts that belong to this group."""
    db = get_db()
    rows = db.execute('''
        SELECT c.* FROM containers c
        JOIN group_members gm ON gm.container_id = c.id
        WHERE gm.group_id = ?
        ORDER BY c.name
    ''', (group_id,)).fetchall()
    return [dict(r) for r in rows]


def get_all_groups_for_container(container_id):
    """Return list of group dicts that contain this container."""
    db = get_db()
    rows = db.execute('''
        SELECT g.* FROM groups g
        JOIN group_members gm ON gm.group_id = g.id
        WHERE gm.container_id = ?
        ORDER BY g.name
    ''', (container_id,)).fetchall()
    return [dict(r) for r in rows]


def add_group_member(group_id, container_id):
    """Assign a container to a group.  Idempotent (INSERT OR IGNORE)."""
    db = get_db()
    db.execute('INSERT OR IGNORE INTO group_members (group_id, container_id) VALUES (?, ?)',
               (group_id, container_id))
    db.commit()


def get_containers_not_in_group(group_id):
    """Return containers that are not members of the given group."""
    db = get_db()
    rows = db.execute('''
        SELECT c.id, c.name, c.container_id, c.status
        FROM containers c
        WHERE c.id NOT IN (
            SELECT gm.container_id FROM group_members gm WHERE gm.group_id = ?
        )
        ORDER BY c.name
    ''', (group_id,)).fetchall()

    # Convert sqlite3.Row -> dict list for JSON serialization
    return [dict(r) for r in rows]


def remove_group_member(group_id, container_id):
    """Remove a container from a group."""
    db = get_db()
    db.execute('DELETE FROM group_members WHERE group_id = ? AND container_id = ?',
               (group_id, container_id))
    db.commit()


# ---------------------------------------------------------------------------
# Group-aware schedule queries
# ---------------------------------------------------------------------------

def get_active_schedules():
    """Return all effective schedules as a flat list of dicts.

    Priority resolution (highest → lowest):
      1. Individual schedule on the container
      2. Group schedule inherited from membership
      3. Default schedule for ungrouped containers without an individual schedule

    Each dict mirrors the schedules table columns plus two extras:
      - schedule_source: 'individual', 'group', or 'default'
      - group_name: present for group schedules (or None)
    """
    db = get_db()

    # --- Individual schedules ---
    individual = db.execute('''
        SELECT s.*, 'individual' AS schedule_source, NULL AS group_name
        FROM schedules s
        WHERE s.container_id IS NOT NULL AND s.enabled = 1
    ''').fetchall()

    # Collect container ids that have individual schedules
    individual_ids = {r['container_id'] for r in individual}

    results = [dict(r) for r in individual]

    # --- Group schedules ---
    group_schedules = db.execute('''
        SELECT s.*, 'group' AS schedule_source, g.name AS group_name
        FROM schedules s
        JOIN groups g ON g.id = s.group_id
        WHERE s.group_id IS NOT NULL AND s.enabled = 1
    ''').fetchall()

    for gs in group_schedules:
        members = db.execute('''
            SELECT c.* FROM containers c
            JOIN group_members gm ON gm.container_id = c.id
            WHERE gm.group_id = ?
        ''', (gs['group_id'],)).fetchall()

        for member in members:
            if member['id'] not in individual_ids:
                row = dict(gs)
                row['container_id'] = member['id']          # target container for this iteration
                row['container_name'] = member['name']      # display name
                row['schedule_type'] = gs['schedule_type']  # schedule info from the group schedule
                row['time_of_day'] = gs['time_of_day']
                row['day_of_week'] = gs['day_of_week']
                row['enabled'] = gs['enabled']
                results.append(row)

    # --- Default schedule (for ungrouped containers without an individual schedule) ---
    default_cfg = get_default_schedule()
    if default_cfg and default_cfg.get('enabled'):
        # Find containers with no individual schedule AND not in any group
        all_with_individual = ','.join(str(i) for i in individual_ids) or '0'
        ungrouped_rows = db.execute('''
            SELECT c.* FROM containers c
            WHERE c.id NOT IN (SELECT gm.container_id FROM group_members gm)
              AND c.id NOT IN ({})
        '''.format(all_with_individual)).fetchall()

        for container in ungrouped_rows:
            row = {
                'container_id': container['id'],
                'container_name': container['name'],
                'schedule_source': 'default',
                'group_name': None,
                'schedule_type': default_cfg.get('schedule_type', 'daily'),
                'time_of_day': default_cfg.get('time_of_day'),
                'day_of_week': int(default_cfg.get('day_of_week', 1)),
                'enabled': default_cfg.get('enabled', True),
                'maintenance_window_start': default_cfg.get('maintenance_window_start'),
                'maintenance_window_end': default_cfg.get('maintenance_window_end'),
            }
            results.append(row)

    return results


def get_schedule_by_group(group_id):
    """Return the schedule assigned to a group, or None."""
    db = get_db()
    row = db.execute(
        'SELECT * FROM schedules WHERE group_id = ? AND enabled = 1', (group_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Default schedule — stored as JSON in the configuration table (key: default_schedule)
# ---------------------------------------------------------------------------

def get_default_schedule():
    """Return the default schedule config dict, or None if not configured.

    Structure::

        {type: 'daily'|'weekly', time_of_day: 'HH:MM', day_of_week: int,
         enabled: bool, maintenance_window_start|end: str,
         auto_sync_proxmox: bool}
    """
    value = get_configuration('default_schedule')
    if not value:
        return None
    import json, os
    try:
        cfg = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    # Ensure booleans
    cfg['enabled'] = bool(cfg.get('enabled', False))
    cfg['auto_sync_proxmox'] = bool(cfg.get('auto_sync_proxmox', False))
    return cfg


def save_default_schedule(schedule_data):
    """Save a default schedule config.  Pass the same fields as individual/group schedules.

    Extra field: auto_sync_proxmox (bool) — sync container list from clusters before patches.
    """
    import json
    entry = {
        'schedule_type': schedule_data.get('schedule_type', 'daily'),
        'time_of_day': schedule_data.get('time_of_day'),
        'day_of_week': int(schedule_data.get('day_of_week', 1)),
        'enabled': bool(schedule_data.get('enabled', True)),
        'maintenance_window_start': schedule_data.get('maintenance_window_start'),
        'maintenance_window_end': schedule_data.get('maintenance_window_end'),
        'auto_sync_proxmox': bool(schedule_data.get('auto_sync_proxmox', False)),
    }
    set_configuration('default_schedule', json.dumps(entry))


def delete_default_schedule():
    """Remove the default schedule configuration."""
    set_configuration('default_schedule', '')


# ---------------------------------------------------------------------------
# Default SSH user — stored as a plain string in the configuration table
# ---------------------------------------------------------------------------

def get_default_ssh_user():
    """Return the configured default SSH user, or 'root' if unset."""
    return get_configuration('default_ssh_user') or 'root'


def set_default_ssh_user(value):
    """Save the default SSH user."""
    set_configuration('default_ssh_user', value)


# ---------------------------------------------------------------------------
# Global discovery settings — stored as simple key-value pairs
# ---------------------------------------------------------------------------

def get_discovery_config():
    """Return a dict of global discovery settings (key -> bool)."""
    return {
        'ignore_172_networks': get_configuration('discovery_ignore_172') == '1',
    }


def set_discovery_config(data):
    """Save discovery config values. Pass {'ignore_172_networks': bool, ...}."""
    if data.get('ignore_172_networks'):
        set_configuration('discovery_ignore_172', '1')
    else:
        set_configuration('discovery_ignore_172', '0')
