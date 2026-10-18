# Patch Management System for Proxmox LXC Containers

This project implements a comprehensive patch management system designed specifically for managing updates to LXC containers in a Proxmox environment.

## Project Overview

The system provides:
- Container management for LXC containers via Proxmox API or manual configuration
- Automated patch scheduling with maintenance windows
- SSH-based patch execution using paramiko library
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
- Docker containerization for easy deployment

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
1. Build Docker image: `docker build -t patch-tuesday .`
2. Run container: `docker run -p 5000:5000 patch-tuesday`

### Command Line Interface
```
patch-tuesday list                    # List all containers
patch-tuesday add "name" "id" [options]  # Add container
patch-tuesday patch <id>              # Apply patches to container
patch-tuesday patch-all               # Apply patches to all containers
```

## Deployment

The system is designed to be containerized using Docker for easy deployment:
- Uses Python 3.12 slim image
- Exposes port 5000 for web interface
- Runs as non-root user for security
- All configuration stored in database

## Future Enhancements

1. Full Proxmox API integration for automatic discovery
2. Enhanced rollback capabilities with better snapshot management  
3. Multi-container synchronization features
4. Advanced alerting (Slack, Discord integrations)
5. Backup system integration for pre-update snapshots
6. Advanced reporting and analytics dashboard