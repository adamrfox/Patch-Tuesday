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
            ('log_retention_days', '30', 'Number of days to retain logs')
        ]

        for key, value, description in default_configs:
            db.execute('''
                INSERT OR IGNORE INTO configuration (key, value, description)
                VALUES (?, ?, ?)
            ''', (key, value, description))

        db.commit()

def get_container_by_id(container_id):
    """Get container by ID"""
    db = get_db()
    return db.execute('SELECT * FROM containers WHERE id = ?', (container_id,)).fetchone()

def get_all_containers():
    """Get all containers"""
    db = get_db()
    return db.execute('SELECT * FROM containers ORDER BY name').fetchall()

def get_container_by_name(name):
    """Get container by name"""
    db = get_db()
    return db.execute('SELECT * FROM containers WHERE name = ?', (name,)).fetchone()

def get_container_by_cid(container_id):
    """Get container by container ID"""
    db = get_db()
    return db.execute('SELECT * FROM containers WHERE container_id = ?', (container_id,)).fetchone()

def save_container(container_data):
    """Save or update a container"""
    db = get_db()

    if 'id' in container_data and container_data['id']:
        # Update existing
        db.execute('''
            UPDATE containers
            SET name=?, container_id=?, status=?, last_updated=?, updated_needs=?,
                ssh_host=?, ssh_port=?, ssh_user=?, snapshot_enabled=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        ''', (
            container_data['name'], container_data['container_id'], container_data['status'],
            container_data.get('last_updated'), container_data.get('updated_needs'),
            container_data.get('ssh_host'), container_data.get('ssh_port'),
            container_data.get('ssh_user'), container_data.get('snapshot_enabled'),
            container_data['id']
        ))
    else:
        # Insert new
        db.execute('''
            INSERT INTO containers (name, container_id, status, last_updated, updated_needs,
                                   ssh_host, ssh_port, ssh_user, snapshot_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            container_data['name'], container_data['container_id'], container_data['status'],
            container_data.get('last_updated'), container_data.get('updated_needs'),
            container_data.get('ssh_host'), container_data.get('ssh_port'),
            container_data.get('ssh_user'), container_data.get('snapshot_enabled')
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