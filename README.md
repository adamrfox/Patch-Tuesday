# Patch Management System for Proxmox LXC Containers

A comprehensive patch management system designed specifically for managing updates to LXC containers in a Proxmox environment.

## Features

- **Container Management**: Discover and manage LXC containers via Proxmox API or manual configuration
- **Patch Scheduling**: Configure automated patch operations with maintenance windows
- **Patch Execution**: Secure execution of apt commands via SSH — or locally, with no SSH at all, when the app runs on the target host itself
- **Snapshot-based Rollback**: Pre-update snapshots for rollback capability (via Proxmox)
- **Alerting System**: Notifications via ntfy and SMTP
- **Web Interface**: User-friendly dashboard for monitoring and management
- **CLI Interface**: Command-line tools for automation and scripting

## Architecture

The system consists of three main components:
1. **Web Interface**: Flask-based web dashboard for management
2. **Patch Engine**: patch execution logic — SSH to remote containers, local subprocess execution when the target is the host the app runs on
3. **Database**: SQLite storage for container information, update history, and schedules

## Installation

### Prerequisites
- Python 3.10+ (or Docker, as an alternative — see below)
- SSH keys configured for remote container access
- A user with passwordless `sudo` on the host, if the host itself is a patch target
- Proxmox environment (for full functionality)

### Setup (native — recommended)

Run the app directly on the host it manages. This is the recommended
deployment: when the app's own host is one of the patch targets, its
patches are executed **locally** (via the host's `sudo`) instead of over
SSH, so no key-to-self setup is needed.

1. Clone or download this repository
2. Create a virtualenv and install dependencies:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Run the app (web UI on port 5000 plus the background patch scheduler):
   ```
   .venv/bin/python -m app
   ```
4. To run it as a persistent service, install the systemd unit (adjust
   `User=`, `WorkingDirectory=`, and `PTU_LOCAL_HOSTS` to match your host):
   ```
   sudo cp deploy/patch-tuesday.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now patch-tuesday
   ```
   Manage it with `systemctl status patch-tuesday` / `journalctl -u patch-tuesday`.

5. Access the web interface at `http://localhost:5000`

### Setup (Docker — alternative)

1. Build the image:
   ```
   docker build -t patch-tuesday .
   ```
2. Run the container:
   ```
   docker run -p 5000:5000 patch-tuesday
   ```
3. Access the web interface at `http://localhost:5000`

> **Note:** a Dockerized app cannot patch the host it lives on "locally" —
> local execution runs inside the container's filesystem. That host entry
> must instead be patched over SSH: put the app user's key in the host's
> `~/.ssh/authorized_keys` and don't list that IP in `PTU_LOCAL_HOSTS`.
> For this reason, run the app natively on any host that is itself a
> patch target.

## Usage

### Web Interface
Access the dashboard at `http://localhost:5000` to:
- View container status
- Add/remove containers
- Configure automatic patching schedules
- Review update history
- Apply patches manually

### CLI Interface
The system includes a command-line interface for automation
(invoke with `python -m app.cli`, run from the repository root):

```
# List all containers
python -m app.cli list

# Add a new container
python -m app.cli add "web-server" "100" --ssh-host "192.168.1.100"

# Apply patches to a specific container
python -m app.cli patch 1

# Apply patches to all containers
python -m app.cli patch-all

# Discover / import containers from a Proxmox cluster
python -m app.cli proxmox-discover --host "192.168.1.10"
python -m app.cli proxmox-import --all --host "192.168.1.10"
```

## Configuration

### SSH Access
The system requires SSH access to **remote** containers. Configure your SSH keys appropriately:
- Place public keys in `~/.ssh/authorized_keys` on target containers
- Ensure containers allow the configured SSH user with key authentication

The host the app itself runs on (if it is a patch target) needs no SSH
setup — its patches execute locally and require only passwordless `sudo`
for the user running the app.

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