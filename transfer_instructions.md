# Patch Management System Transfer Instructions

## Repository Setup

You've created the repository at: https://github.com/adamrfox/Patch-Tuesday

## Manual File Transfer Steps

1. **Download all files from this directory**:
   - You can download the entire patch-tuesday directory as a zip file
   - Or copy individual files manually

2. **Files to transfer**:

### Core Application Files:
- `app/database.py` 
- `app/container_manager.py`
- `app/patch_engine.py`
- `app/scheduler.py`
- `app/alerting.py`
- `app/cli.py`
- `app/routes.py`
- `app/__init__.py`
- `app/__main__.py`

### Web Interface:
- `web/templates/index.html`

### Configuration Files:
- `Dockerfile`
- `requirements.txt`
- `README.md`
- `CLAUDE.md`
- `.gitignore`

3. **Setup your local repository**:
```bash
mkdir patch-tuesday-local
cd patch-tuesday-local
git init
git remote add origin https://github.com/adamrfox/Patch-Tuesday.git
```

4. **Add and commit all files**:
```bash
# Copy all downloaded files to this directory
git add .
git commit -m "Initial commit: Patch Management System for Proxmox LXC Containers"
git push -u origin master
```

## System Features

This system provides a complete patch management solution for Proxmox LXC containers with:

- SSH-based patching of containers
- Automated scheduling with maintenance windows  
- Snapshot rollback capability (via Proxmox)
- Web dashboard for monitoring
- CLI interface for automation
- Alerting system (ntfy, SMTP)
- Docker containerization

## Deployment

The system can be deployed as a Docker container:
```bash
docker build -t patch-tuesday .
docker run -p 5000:5000 patch-tuesday
```

## Usage

### Web Interface
Access at http://localhost:5000

### CLI Interface
```bash
patch-tuesday list
patch-tuesday add "container-name" "container-id"
patch-tuesday patch 1
patch-tuesday patch-all
```