# Patch Management System for Proxmox LXC Containers

This project implements a comprehensive patch management system designed specifically for managing updates to LXC containers in a Proxmox environment.

## Project Overview

The system provides:
- Container management for LXC containers via Proxmox API or manual configuration
- Automated patch scheduling with maintenance windows
- Patch execution using paramiko SSH — or locally (subprocess) when the target is the host the app itself runs on
- Snapshot-based rollback capability (via Proxmox)
- Alerting system via ntfy and SMTP
- Web interface for monitoring and management
- CLI interface for automation and scripting

## Key Components

### Core Modules
1. **Database**: SQLite-based storage for container information, update history, and schedules
2. **Container Manager**: Handles communication with LXC containers
3. **Patch Engine**: Executes patch operations via SSH
4. **Scheduler**: Manages automated patch operations
5. **Alerting System**: Notifies users of patch events
6. **CLI Interface**: Command-line tools for automation
7. **Web Interface**: Flask-based dashboard

### Features Implemented
- Add/remove containers manually or discover from Proxmox
- Configure automatic patch schedules with time windows
- Apply patches via SSH to LXC containers
- View update history and logs
- Notification system (ntfy, SMTP)
- Native deployment via systemd (`deploy/patch-tuesday.service`), Docker as an alternative

## Technical Details

### Architecture
The system uses a modular approach with:
- Flask web framework for the UI
- SQLite database for persistence
- Paramiko library for secure SSH connections
- Jinja2 templating for web interface
- Cron-based scheduling via python-crontab

### Security Considerations
- SSH keys used instead of passwords for container access
- All operations logged for audit purposes
- Container access restricted to authorized users
- Web interface requires proper authentication in production

## Usage

### Running the Application
Native (recommended — required when the app's own host is a patch target):
1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. `.venv/bin/python -m app` — web UI on port 5000 + background scheduler
3. For persistence, install `deploy/patch-tuesday.service`:
   `sudo cp deploy/patch-tuesday.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now patch-tuesday`

Docker (alternative — do NOT use it on a host that is itself a patch target,
see the note in README):
1. Build Docker image: `docker build -t patch-tuesday .`
2. Run container: `docker run -p 5000:5000 patch-tuesday`

### Command Line Interface
```
python -m app.cli list                    # List all containers
python -m app.cli add "name" "id" [options]  # Add container
python -m app.cli patch <id>              # Apply patches to container
python -m app.cli patch-all               # Apply patches to all containers
```

## Deployment

The system runs natively as a systemd service (see `deploy/patch-tuesday.service`)
on the host it manages. The running user needs passwordless `sudo` for local
patch execution on that host; remote containers are patched over SSH.
Docker remains available as an alternative deployment for hosts that are
not themselves patch targets:
- Uses Python 3.12 slim image
- Exposes port 5000 for web interface
- All configuration stored in database

## Future Enhancements

1. Full Proxmox API integration for automatic discovery
2. Enhanced rollback capabilities with better snapshot management  
3. Multi-container synchronization features
4. Advanced alerting (Slack, Discord integrations)
5. Backup system integration for pre-update snapshots
6. Advanced reporting and analytics dashboard