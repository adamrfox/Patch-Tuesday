# Patch Management System for Proxmox LXC Containers

A comprehensive patch management system designed specifically for managing updates to LXC containers in a Proxmox environment.

## Features

- **Container Management**: Discover and manage LXC containers via Proxmox API or manual configuration
- **Patch Scheduling**: Configure automated patch operations with maintenance windows
- **SSH-based Patching**: Secure execution of apt commands on containers via SSH
- **Snapshot-based Rollback**: Pre-update snapshots for rollback capability (via Proxmox)
- **Alerting System**: Notifications via ntfy and SMTP
- **Web Interface**: User-friendly dashboard for monitoring and management
- **CLI Interface**: Command-line tools for automation and scripting

## Architecture

The system consists of three main components:
1. **Web Interface**: Flask-based web dashboard for management
2. **Patch Engine**: SSH communication and patch execution logic
3. **Database**: SQLite storage for container information, update history, and schedules

## Installation

### Prerequisites
- Docker installed on the host system
- SSH keys configured for container access
- Proxmox environment (for full functionality)

### Setup

1. Clone or download this repository
2. Build the Docker image:
   ```
   docker build -t patch-tuesday .
   ```

3. Run the container:
   ```
   docker run -p 5000:5000 patch-tuesday
   ```

4. Access the web interface at `http://localhost:5000`

## Usage

### Web Interface
Access the dashboard at `http://localhost:5000` to:
- View container status
- Add/remove containers
- Configure automatic patching schedules
- Review update history
- Apply patches manually

### CLI Interface
The system includes a command-line interface for automation:

```
# List all containers
patch-tuesday list

# Add a new container
patch-tuesday add "web-server" "100" --ssh-host "192.168.1.100"

# Apply patches to a specific container
patch-tuesday patch 1

# Apply patches to all containers
patch-tuesday patch-all
```

## Configuration

### SSH Access
The system requires SSH access to containers. Configure your SSH keys appropriately:
- Place public keys in `/root/.ssh/authorized_keys` on target containers
- Ensure containers allow root SSH login with key authentication

### Alerting
Configure notification settings via the web interface or database:
- ntfy URL for real-time notifications
- SMTP configuration for email alerts

## Security Considerations

- The system is designed to be internet-exposed only in controlled environments
- SSH keys are used for container communication (no passwords)
- All operations logged for audit purposes
- Container access restricted to authorized users

## Maintenance Windows

Configure maintenance windows to prevent patching during peak hours:
- Set specific time ranges when automatic updates are disabled
- Manual overrides allowed with warning notifications

## Future Enhancements

1. **Proxmox API Integration**: Full Proxmox container discovery and management
2. **Advanced Rollback**: Enhanced rollback capabilities with better snapshot management
3. **Multi-Container Sync**: Synchronization of patch operations across containers
4. **Advanced Alerting**: Slack, Discord, and other notification integrations
5. **Backup Management**: Integration with backup systems for pre-update snapshots

## Contributing

This project is designed as a starting point for container patch management in Proxmox environments. Contributions to enhance functionality, security, or usability are welcome.

## License

MIT License