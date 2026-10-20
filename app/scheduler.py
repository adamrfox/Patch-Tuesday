"""
Scheduling system for patch operations
"""
import logging
from datetime import datetime, time
from croniter import croniter
from .database import get_active_schedules, get_container_by_id, delete_schedule as db_delete_schedule, save_schedule
from .patch_engine import patch_engine
from .alerting import alerting_system

logger = logging.getLogger(__name__)

class Scheduler:
    """Handles scheduling of patch operations"""

    def __init__(self):
        self.schedules = []

    def load_schedules(self):
        """Load all active schedules from database"""
        # This would typically be called at startup
        self.schedules = get_active_schedules()
        logger.info(f"Loaded {len(self.schedules)} effective schedules")

    def is_schedule_active(self, schedule):
        """Check if a schedule is currently active (within maintenance window)"""
        if not schedule['enabled']:
            return False

        # Check maintenance window
        if schedule.get('maintenance_window_start') and schedule.get('maintenance_window_end'):
            try:
                start_time = datetime.strptime(schedule['maintenance_window_start'], '%H:%M').time()
                end_time = datetime.strptime(schedule['maintenance_window_end'], '%H:%M').time()

                current_time = datetime.now().time()

                # Handle case where window crosses midnight
                if start_time <= end_time:
                    # Window within same day
                    return start_time <= current_time <= end_time
                else:
                    # Window crosses midnight
                    return current_time >= start_time or current_time <= end_time
            except ValueError:
                logger.error("Invalid time format in maintenance window")
                return True  # Default to allowing updates if time parsing fails

        return True  # No maintenance window defined, so allow updates

    def get_next_run_time(self, schedule):
        """Get the next scheduled run time for a schedule"""
        try:
            # For now we'll just use basic cron parsing
            # In a more complete implementation, this would be more sophisticated
            if schedule['schedule_type'] == 'daily':
                cron_string = f"0 {schedule['time_of_day'].split(':')[0]} * * *"
            elif schedule['schedule_type'] == 'weekly':
                cron_string = f"0 {schedule['time_of_day'].split(':')[0]} * * {schedule['day_of_week']}"
            else:
                # Custom schedule - parse as cron expression
                cron_string = schedule['time_of_day']  # Expecting cron format

            cron = croniter(cron_string, datetime.now())
            return cron.get_next(datetime)
        except Exception as e:
            logger.error(f"Error calculating next run time: {str(e)}")
            return None

    def run_scheduled_patches(self):
        """Run all scheduled patch operations.

        If the config key 'auto_sync_proxmox' is enabled and a default-group schedule
        is being evaluated, first scan all configured clusters and sync the container list.
        """
        self.load_schedules()

        results = []
        patched = set()  # track container_ids already patched this pass

        # Check if auto-sync is enabled (config key in configuration table)
        from .database import get_db, get_default_schedule
        cfg = get_default_schedule()
        auto_sync = bool(cfg.get('auto_sync_proxmox', False)) if cfg else False
        sync_done = False

        for schedule in self.schedules:
            try:
                cid = schedule['container_id']
                if cid in patched:
                    logger.debug(f"Skipping {cid} — already patched by another schedule")
                    continue

                # Check if schedule is active
                if not self.is_schedule_active(schedule):
                    continue

                container = get_container_by_id(cid)
                if not container:
                    logger.warning(f"Container {cid} not found for schedule {schedule.get('id')}")
                    continue

                # Check if it's time to run
                next_run = self.get_next_run_time(schedule)
                if not next_run:
                    continue

                # Auto-sync before running default-group schedules (once per pass)
                source = schedule.get('schedule_source', 'unknown')
                if auto_sync and not sync_done and source == 'default':
                    try:
                        from .container_manager import container_manager
                        sync_result = container_manager.sync_default_group()
                        logger.info(
                            f"Cluster sync before default-group patches: "
                            f"added={sync_result['added']}, removed={sync_result['removed']}"
                        )
                    except Exception as e:
                        logger.error(f"Auto-sync failed: {e}")
                    finally:
                        sync_done = True

                logger.info(f"Running scheduled patch for {container['name']} (source: {source})")

                result = patch_engine.apply_patches(cid)
                patched.add(cid)
                results.append({
                    'container': container['name'],
                    'schedule_id': schedule['id'],
                    'result': result
                })

                # Skip notifications when nothing was actually updated
                if not result.get('skipped'):
                    status = 'success' if result.get('success') else 'failed'
                    alerting_system.send_update_notification(
                        container['name'],
                        status,
                        packages_updated=result.get('packages_updated'),
                        error_message=result.get('error'),
                    )

            except Exception as e:
                logger.error(f"Error running scheduled patch for {schedule.get('container_id', 'unknown')}: {str(e)}")
                continue

        return results

    def add_schedule(self, container_id=None, schedule_type='daily', time_of_day='02:00',
                    day_of_week=1, enabled=True, maintenance_window_start=None,
                    maintenance_window_end=None, group_id=None):
        """Add a new schedule (individual or group)."""
        schedule_data = {
            'container_id': container_id,
            'group_id': group_id,
            'schedule_type': schedule_type,
            'time_of_day': time_of_day,
            'day_of_week': day_of_week,
            'enabled': enabled,
            'maintenance_window_start': maintenance_window_start,
            'maintenance_window_end': maintenance_window_end,
        }
        save_schedule(schedule_data)

    def update_schedule(self, schedule_id, **kwargs):
        """Update an existing schedule.  Routes call save_schedule directly."""
        pass

    def delete_schedule(self, schedule_id):
        """Delete a schedule."""
        db_delete_schedule(schedule_id)

# Global instance for use in the application
scheduler = Scheduler()