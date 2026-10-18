"""
CLI interface for patch management system
"""
import sys
import argparse
from .container_manager import container_manager
from .patch_engine import patch_engine
from .database import get_all_containers, get_container_by_id, save_container
from .alerting import alerting_system

def list_containers():
    """List all managed containers"""
    containers = get_all_containers()
    if not containers:
        print("No containers configured.")
        return

    print(f"{'ID':<5} {'Name':<20} {'Container ID':<12} {'Status':<12} {'SSH Host':<15}")
    print("-" * 70)
    for container in containers:
        print(f"{container['id']:<5} {container['name']:<20} {container['container_id']:<12} "
              f"{container['status']:<12} {container.get('ssh_host', 'N/A'):<15}")

def add_container(name, container_id, ssh_host=None, ssh_port=22, ssh_user='root'):
    """Add a new container"""
    try:
        container = container_manager.add_container(
            name=name,
            container_id=container_id,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user
        )
        print(f"Successfully added container: {container['name']}")
    except Exception as e:
        print(f"Error adding container: {str(e)}")

def remove_container(container_id):
    """Remove a container"""
    try:
        # In a real implementation, we'd also clean up associated data
        container_manager.remove_container(container_id)
        print(f"Successfully removed container with ID: {container_id}")
    except Exception as e:
        print(f"Error removing container: {str(e)}")

def update_container(container_id, ssh_host=None, ssh_port=None, ssh_user=None):
    """Update container configuration"""
    try:
        container = get_container_by_id(container_id)
        if not container:
            print(f"Container with ID {container_id} not found")
            return

        # Update the container data
        container_data = {
            'id': container['id'],
            'name': container['name'],
            'container_id': container['container_id'],
            'status': container['status'],
            'ssh_host': ssh_host or container['ssh_host'],
            'ssh_port': ssh_port or container['ssh_port'],
            'ssh_user': ssh_user or container['ssh_user']
        }

        save_container(container_data)
        print(f"Successfully updated container: {container['name']}")
    except Exception as e:
        print(f"Error updating container: {str(e)}")

def patch_container(container_id, dry_run=False):
    """Apply patches to a container"""
    try:
        if dry_run:
            print("Dry run - checking for updates...")
            result = patch_engine.check_updates_available(container_id)
            if result and 'error' not in result:
                print(f"Updates available for {container_id}")
            else:
                print(f"No updates or error: {result.get('error', 'Unknown error')}")
        else:
            print("Applying patches...")
            result = patch_engine.apply_patches(container_id)

            if result['success']:
                print(f"Successfully patched container {container_id}")
                alerting_system.send_update_notification(
                    container_id,
                    'success',
                    packages_updated=result.get('packages_updated')
                )
            else:
                print(f"Failed to patch container {container_id}: {result.get('error', 'Unknown error')}")
                alerting_system.send_update_notification(
                    container_id,
                    'failed',
                    error_message=result.get('error')
                )
    except Exception as e:
        print(f"Error during patch operation: {str(e)}")

def patch_all_containers(dry_run=False):
    """Apply patches to all containers"""
    try:
        containers = get_all_containers()
        if not containers:
            print("No containers configured")
            return

        print(f"Starting patch operation on {len(containers)} containers...")

        for container in containers:
            print(f"Patching {container['name']}...")
            if dry_run:
                result = patch_engine.check_updates_available(container['id'])
                if result and 'error' not in result:
                    print(f"  Updates available for {container['name']}")
                else:
                    print(f"  No updates or error: {result.get('error', 'Unknown error')}")
            else:
                result = patch_engine.apply_patches(container['id'])

                if result['success']:
                    print(f"  ✓ Successfully patched {container['name']}")
                else:
                    print(f"  ✗ Failed to patch {container['name']}: {result.get('error', 'Unknown error')}")

        print("Patch operation completed.")
    except Exception as e:
        print(f"Error during batch patch operation: {str(e)}")

def discover_containers(proxmox_host, proxmox_user=None, proxmox_password=None,
                       proxmox_port=8006, verify_ssl=False, token_name=None, token_value=None):
    """Discover and display containers from a Proxmox cluster."""
    try:
        discovered = container_manager.discover_containers_from_proxmox(
            proxmox_host=proxmox_host,
            proxmox_user=proxmox_user,
            proxmox_password=proxmox_password,
            proxmox_port=proxmox_port,
            verify_ssl=verify_ssl,
            token_name=token_name,
            token_value=token_value,
        )
    except Exception as e:
        print(f"Error discovering containers: {e}")
        return

    if not discovered:
        print("No containers discovered. Check connection settings.")
        return

    print(f"\nDiscovered {len(discovered)} container(s) from Proxmox ({proxmox_host}):")
    print(f"{'ID':<8} {'Name':<25} {'Status':<12} {'Node':<12} {'IPs':<30} {'MAC'}")
    print("-" * 107)
    for c in discovered:
        ips_str = ', '.join(c.get('all_ips', []) or []) or 'N/A'
        macs = c.get('mac_addresses', []) or []
        mac_str = ', '.join(macs) if macs else 'N/A'
        print(f"{c['container_id']:<8} {c['name']:<25} {c['status']:<12} "
              f"{c['node']:<12} {ips_str:<30} {mac_str}")


def import_containers(proxmox_host, proxmox_user=None, proxmox_password=None,
                      proxmox_port=8006, verify_ssl=False, token_name=None, token_value=None,
                      selected_ids=None, ssh_user='root', ssh_port=22):
    """Discover and optionally import containers from a Proxmox cluster."""
    try:
        discovered = container_manager.discover_containers_from_proxmox(
            proxmox_host=proxmox_host,
            proxmox_user=proxmox_user,
            proxmox_password=proxmox_password,
            proxmox_port=proxmox_port,
            verify_ssl=verify_ssl,
            token_name=token_name,
            token_value=token_value,
        )
    except Exception as e:
        print(f"Error discovering containers: {e}")
        return

    if not discovered:
        print("No containers discovered. Cannot import.")
        return

    # Filter by selected IDs
    to_import = discovered
    if selected_ids is not None and len(selected_ids) > 0:
        selected_set = set(str(s) for s in selected_ids)
        to_import = [c for c in discovered if c['container_id'] in selected_set]
        skipped = [c for c in discovered if c['container_id'] not in selected_set]
        print(f"Discovered {len(discovered)} container(s). Importing {len(to_import)}, skipping {len(skipped)}.")
    else:
        print(f"Discovered {len(discovered)} container(s).")

    if not to_import:
        print("No containers selected for import.")
        return

    result = container_manager.import_proxmox_containers(
        to_import, ssh_user=ssh_user, ssh_port=ssh_port
    )

    print(f"\nImport complete:")
    print(f"  Imported: {result['imported']}")
    if result['skipped']:
        print(f"  Skipped (already exists): {result['skipped']}")
    if result['errors']:
        for err in result['errors']:
            print(f"  Error: {err}")


def show_help():
    help_text = """
Patch Management CLI Tool

Usage:
  list                     - List all managed containers
  add <name> <id> [options] - Add a new container
  remove <id>             - Remove a container
  update <id> [options]   - Update container configuration
  patch <id>              - Apply patches to a container
  patch-all               - Apply patches to all containers
  proxmox-discover        - Discover containers from a Proxmox cluster
  proxmox-import          - Import discovered containers into the database
  help                    - Show this help

Options for 'add':
  --ssh-host HOST        - SSH host (default: None)
  --ssh-port PORT        - SSH port (default: 22)
  --ssh-user USER        - SSH user (default: root)

Options for 'update':
  --ssh-host HOST        - SSH host
  --ssh-port PORT        - SSH port
  --ssh-user USER        - SSH user

Examples:
  patch-tuesday list
  patch-tuesday add "web-server" "100" --ssh-host "192.168.1.100"
  patch-tuesday patch 1
  patch-tuesday patch-all
  patch-tuesday proxmox-discover --host "192.168.1.10"
  patch-tuesday proxmox-import --all --host "192.168.1.10"
  patch-tuesday proxmox-import --container 100 --container 205 --host "192.168.1.10"
"""
    print(help_text)

def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()

    try:
        if command == 'list':
            list_containers()

        elif command == 'add':
            if len(sys.argv) < 4:
                print("Error: add command requires name and container ID")
                return

            name = sys.argv[2]
            container_id = sys.argv[3]

            # Parse optional arguments
            ssh_host = None
            ssh_port = 22
            ssh_user = 'root'

            for i in range(4, len(sys.argv)):
                if sys.argv[i] == '--ssh-host' and i + 1 < len(sys.argv):
                    ssh_host = sys.argv[i + 1]
                elif sys.argv[i] == '--ssh-port' and i + 1 < len(sys.argv):
                    ssh_port = int(sys.argv[i + 1])
                elif sys.argv[i] == '--ssh-user' and i + 1 < len(sys.argv):
                    ssh_user = sys.argv[i + 1]

            add_container(name, container_id, ssh_host, ssh_port, ssh_user)

        elif command == 'remove':
            if len(sys.argv) < 3:
                print("Error: remove command requires container ID")
                return
            remove_container(int(sys.argv[2]))

        elif command == 'update':
            if len(sys.argv) < 3:
                print("Error: update command requires container ID")
                return

            container_id = int(sys.argv[2])

            # Parse optional arguments
            ssh_host = None
            ssh_port = None
            ssh_user = None

            for i in range(3, len(sys.argv)):
                if sys.argv[i] == '--ssh-host' and i + 1 < len(sys.argv):
                    ssh_host = sys.argv[i + 1]
                elif sys.argv[i] == '--ssh-port' and i + 1 < len(sys.argv):
                    ssh_port = int(sys.argv[i + 1])
                elif sys.argv[i] == '--ssh-user' and i + 1 < len(sys.argv):
                    ssh_user = sys.argv[i + 1]

            update_container(container_id, ssh_host, ssh_port, ssh_user)

        elif command == 'patch':
            if len(sys.argv) < 3:
                print("Error: patch command requires container ID")
                return
            patch_container(int(sys.argv[2]), dry_run=False)

        elif command == 'patch-all':
            dry_run = '--dry-run' in sys.argv
            patch_all_containers(dry_run)

        elif command == 'proxmox-discover':
            if len(sys.argv) < 3:
                print("Error: proxmox-discover requires a Proxmox host.")
                print("Usage: patch-tuesday proxmox-discover --host HOST [options]")
                print("Options:")
                print("  --host HOST          - Proxmox cluster hostname (required)")
                print("  --user USER          - Username, e.g. root@pam")
                print("  --password PASS      - Password (fallback if no token)")
                print("  --port PORT          - API port (default: 8006)")
                print("  --token-name NAME    - API token key name")
                print("  --token-value VAL    - API token value")
                print("  --insecure           - Skip SSL verification")
                return

            proxmox_host = None
            proxmox_user = None
            proxmox_password = None
            proxmox_port = 8006
            verify_ssl = True
            token_name = None
            token_value = None

            i = 2
            while i < len(sys.argv):
                if sys.argv[i] == '--host' and i + 1 < len(sys.argv):
                    proxmox_host = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--user' and i + 1 < len(sys.argv):
                    proxmox_user = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--password' and i + 1 < len(sys.argv):
                    proxmox_password = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--port' and i + 1 < len(sys.argv):
                    try:
                        proxmox_port = int(sys.argv[i + 1])
                    except ValueError:
                        print(f"Invalid port: {sys.argv[i + 1]}")
                        return
                    i += 2
                elif sys.argv[i] == '--token-name' and i + 1 < len(sys.argv):
                    token_name = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--token-value' and i + 1 < len(sys.argv):
                    token_value = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] in ('--insecure', '-k'):
                    verify_ssl = False
                    i += 1
                else:
                    print(f"Unknown option: {sys.argv[i]}")
                    return

            if not proxmox_host:
                print("Error: --host is required for proxmox-discover")
                return

            discover_containers(proxmox_host, proxmox_user, proxmox_password,
                              proxmox_port, verify_ssl, token_name, token_value)

        elif command == 'proxmox-import':
            if len(sys.argv) < 3:
                print("Error: proxmox-import requires a Proxmox host.")
                print("Usage: patch-tuesday proxmox-import --host HOST [options]")
                print("Options:")
                print("  --host HOST          - Proxmox cluster hostname (required)")
                print("  --all                - Import ALL discovered containers")
                print("  --container ID       - Import specific container(s) (repeatable, by vmid)")
                print("  --user USER          - Username, e.g. root@pam")
                print("  --password PASS      - Password (fallback if no token)")
                print("  --port PORT          - API port (default: 8006)")
                print("  --token-name NAME    - API token key name")
                print("  --token-value VAL    - API token value")
                print("  --insecure           - Skip SSL verification")
                return

            proxmox_host = None
            proxmox_user = None
            proxmox_password = None
            proxmox_port = 8006
            verify_ssl = True
            token_name = None
            token_value = None
            import_all = False
            selected_ids = []

            i = 2
            while i < len(sys.argv):
                if sys.argv[i] == '--host' and i + 1 < len(sys.argv):
                    proxmox_host = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--all':
                    import_all = True
                    i += 1
                elif sys.argv[i] in ('--container', '-c') and i + 1 < len(sys.argv):
                    selected_ids.append(sys.argv[i + 1])
                    i += 2
                elif sys.argv[i] == '--user' and i + 1 < len(sys.argv):
                    proxmox_user = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--password' and i + 1 < len(sys.argv):
                    proxmox_password = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--port' and i + 1 < len(sys.argv):
                    try:
                        proxmox_port = int(sys.argv[i + 1])
                    except ValueError:
                        print(f"Invalid port: {sys.argv[i + 1]}")
                        return
                    i += 2
                elif sys.argv[i] == '--token-name' and i + 1 < len(sys.argv):
                    token_name = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == '--token-value' and i + 1 < len(sys.argv):
                    token_value = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] in ('--insecure', '-k'):
                    verify_ssl = False
                    i += 1
                else:
                    print(f"Unknown option: {sys.argv[i]}")
                    return

            if not proxmox_host:
                print("Error: --host is required for proxmox-import")
                return

            if not import_all and not selected_ids:
                print("Error: use --all to import all discovered, or specify containers with --container ID (repeatable)")
                return

            final_ids = None if import_all else selected_ids
            import_containers(proxmox_host, proxmox_user, proxmox_password,
                            proxmox_port, verify_ssl, token_name, token_value,
                            selected_ids=final_ids)

        elif command == 'help':
            show_help()

        else:
            print(f"Unknown command: {command}")
            show_help()

    except Exception as e:
        print(f"Error running command: {str(e)}")

if __name__ == '__main__':
    main()