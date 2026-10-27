"""
Scheduling system for patch operations

A background daemon thread (started via ``Scheduler.start``) wakes every
minute and fires each enabled schedule whose next slot is due:

  * individual - a row in the ``schedules`` table for a single container
  * group      - a row in the ``schedules`` table for a group (applies to
                 all members, except those with their own individual schedule)
  * default    - the ``default_schedule`` configuration (applies to
                 ungrouped containers without an individual schedule)

A schedule only fires when the current time has passed its next slot
(computed from the stored last-run timestamp) and, when a maintenance
window is defined, the current time falls inside that window.  Last-run
timestamps live in the ``configuration`` table so they survive restarts.
"""
import logging
import threading
import time
from datetime import datetime

from croniter import croniter

from .database import (
    get_configuration,
    set_configuration,
    get_container_by_id,
    get_group_members,
    get_all_containers,
    get_default_schedule,
    delete_schedule as db_delete_schedule,
    save_schedule,
)
from .patch_engine import patch_engine
from .alerting import alerting_system

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


class Scheduler:
    """Handles scheduling of patch operations"""

    def __init__(self):
        self._thread = None

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def start(self, app):
        """Start the background scheduler thread (no-op if already running)."""
        if self._thread and self._thread.is_alive():
            return
        logger.info("Starting patch scheduler (checking every %ss)", CHECK_INTERVAL_SECONDS)
        self._thread = threading.Thread(
            target=self._loop, args=(app,), name="patch-scheduler", daemon=True
        )
        self._thread.start()

    def _loop(self, app):
        while True:
            try:
                with app.app_context():
                    self.run_scheduled_patches()
            except Exception as e:
                logger.error(f"Scheduler pass failed: {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Schedule resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _cron_string(schedule):
        """Build a cron expression from a schedule dict, or None if invalid."""
        schedule_type = schedule.get('schedule_type', 'daily')
        if schedule_type == 'custom':
            # Stored verbatim as a cron expression
            return schedule.get('time_of_day')
        tod = schedule.get('time_of_day') or '02:00'
        parts = tod.split(':')
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return None
        if schedule_type == 'weekly':
            day = int(schedule.get('day_of_week', 1))
            return f"{minute} {hour} * * {day}"
        return f"{minute} {hour} * * *"

    def _resolve_schedules(self):
        """Resolve all enabled schedules to (scope_key, schedule, [container_ids]).

        Priority: individual > group > default.  A container is only
        patched once per pass, under its highest-priority schedule.
        """
        from .database import get_db
        db = get_db()
        targets = []
        individual_ids = set()

        # 1) Individual schedules
        for row in db.execute('SELECT * FROM schedules WHERE container_id IS NOT NULL AND enabled = 1'):
            s = dict(row)
            individual_ids.add(s['container_id'])
            targets.append((f"individual_{s['id']}", s, [s['container_id']]))

        # 2) Group schedules (members without an individual override)
        for row in db.execute('SELECT * FROM schedules WHERE group_id IS NOT NULL AND enabled = 1'):
            s = dict(row)
            member_ids = [c['id'] for c in get_group_members(s['group_id'])
                          if c['id'] not in individual_ids]
            if member_ids:
                targets.append((f"group_{s['group_id']}", s, member_ids))
            else:
                logger.debug(f"Group schedule {s['id']}: no effective members — skipping")

        # 3) Default schedule (ungrouped containers without individual schedules)
        cfg = get_default_schedule()
        if cfg and cfg.get('enabled'):
            grouped = {r[0] for r in db.execute('SELECT container_id FROM group_members')}
            default_ids = [c['id'] for c in get_all_containers()
                           if c['id'] not in grouped and c['id'] not in individual_ids]
            if default_ids:
                targets.append(('default', cfg, default_ids))
            else:
                logger.debug("Default schedule: no ungrouped containers — skipping")

        return targets

    # ------------------------------------------------------------------
    # Due checking
    # ------------------------------------------------------------------

    def _due_info(self, scope_key, schedule):
        """Check whether a schedule is due to run now.

        Returns (due, next_run).  On the first observation (no last-run
        recorded) it just starts tracking: the first patch happens at the
        next scheduled slot, not immediately.
        """
        last_raw = get_configuration(f'sched_last_run_{scope_key}')
        try:
            last_run = datetime.fromisoformat(last_raw) if last_raw else None
        except ValueError:
            last_run = None

        cron_string = self._cron_string(schedule)
        if not cron_string:
            logger.error(f"Schedule {scope_key}: invalid time format — skipping")
            return False, None

        now = datetime.now()
        if last_run is None:
            set_configuration(f'sched_last_run_{scope_key}', now.isoformat())
            next_run = croniter(cron_string, now).get_next(datetime)
            logger.info(f"Schedule {scope_key}: first observation — tracking starts now, "
                        f"next run {next_run}")
            return False, next_run

        next_run = croniter(cron_string, last_run).get_next(datetime)
        return next_run <= now, next_run

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_scheduled_patches(self, force=False):
        """Run one scheduler pass: fire every schedule that is due now.

        If auto_sync_proxmox is enabled and the default schedule fires,
        scan the configured Proxmox clusters first (once per pass).
        """
        targets = self._resolve_schedules()
        results = []
        patched = set()  # container ids already patched this pass

        auto_sync = bool((get_default_schedule() or {}).get('auto_sync_proxmox'))
        sync_done = False

        for scope_key, schedule, container_ids in targets:
            due, next_run = (True, None) if force else self._due_info(scope_key, schedule)
            if not due:
                logger.debug(f"Schedule {scope_key}: not due (next run {next_run})")
                continue

            if not force and not self.is_schedule_active(schedule):
                logger.debug(f"Schedule {scope_key}: due but outside maintenance window — "
                             f"will retry next tick")
                continue

            if scope_key == 'default' and auto_sync and not sync_done:
                try:
                    from .container_manager import container_manager
                    sync_result = container_manager.sync_default_group()
                    logger.info(f"Cluster sync before default-group patches: "
                                f"added={sync_result['added']}, removed={sync_result['removed']}")
                except Exception as e:
                    logger.error(f"Auto-sync failed: {e}")
                finally:
                    sync_done = True

            logger.info(f"Running schedule {scope_key}: {len(container_ids)} container(s)")

            for cid in container_ids:
                if cid in patched:
                    logger.debug(f"Skipping {cid} — already patched by another schedule")
                    continue
                try:
                    container = get_container_by_id(cid)
                    if not container:
                        logger.warning(f"Container {cid} not found for schedule {scope_key}")
                        continue

                    logger.info(f"Running scheduled patch for {container['name']} "
                                f"(scope: {scope_key})")
                    result = patch_engine.apply_patches(cid)
                    patched.add(cid)
                    results.append({'container': container['name'], 'scope': scope_key,
                                    'result': result})

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
                    logger.error(f"Error running scheduled patch for {cid}: {str(e)}")
                    continue

            # Mark as run so this slot doesn't fire again
            if not force:
                set_configuration(f'sched_last_run_{scope_key}', datetime.now().isoformat())
                logger.info(f"Schedule {scope_key}: marked as run")

        return results

    # ------------------------------------------------------------------
    # Legacy helpers (kept for compatibility)
    # ------------------------------------------------------------------

    def is_schedule_active(self, schedule):
        """Check if a schedule is currently active (within maintenance window)"""
        if not schedule.get('enabled', True):
            return False

        if schedule.get('maintenance_window_start') and schedule.get('maintenance_window_end'):
            try:
                start_time = datetime.strptime(schedule['maintenance_window_start'], '%H:%M').time()
                end_time = datetime.strptime(schedule['maintenance_window_end'], '%H:%M').time()
                current_time = datetime.now().time()

                # Handle case where window crosses midnight
                if start_time <= end_time:
                    return start_time <= current_time <= end_time
                return current_time >= start_time or current_time <= end_time
            except ValueError:
                logger.error("Invalid time format in maintenance window")
                return True  # Default to allowing updates if time parsing fails

        return True  # No maintenance window defined, so allow updates

    def get_next_run_time(self, schedule):
        """Get the next scheduled run time for a schedule"""
        cron_string = self._cron_string(schedule)
        if not cron_string:
            return None
        try:
            return croniter(cron_string, datetime.now()).get_next(datetime)
        except Exception as e:
            logger.error(f"Error calculating next run time: {str(e)}")
            return None

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

    def delete_schedule(self, schedule_id):
        """Delete a schedule."""
        db_delete_schedule(schedule_id)


# Global instance for use in the application
scheduler = Scheduler()
