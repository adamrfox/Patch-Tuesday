"""
Scheduling system for patch operations
"""
import logging
from datetime import datetime, time
from croniter import croniter
from .database import get_all_schedules, get_container_by_id
from .patch_engine import patch_engine

logger = logging.getLogger(__name__)

class Scheduler:
    """Handles scheduling of patch operations"""

    def __init__(self):
        self.schedules = []

    def load_schedules(self):
        """Load all schedules from database"""
        # This would typically be called at startup
        self.schedules = get_all_schedules()
        logger.info(f"Loaded {len(self.schedules)} schedules")

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
        """Run all scheduled patch operations"""
        self.load_schedules()

        results = []
        for schedule in self.schedules:
            try:
                # Check if schedule is active
                if not self.is_schedule_active(schedule):
                    continue

                # Get the container for this schedule
                container = get_container_by_id(schedule['container_id'])
                if not container:
                    logger.warning(f"Container {schedule['container_id']} not found for schedule")
                    continue

                # Check if it's time to run
                next_run = self.get_next_run_time(schedule)
                if not next_run:
                    continue

                # For simplicity, we'll just run the patch now (in a real system,
                # this would be triggered by cron or a background task)
                logger.info(f"Running scheduled patch for {container['name']}")

                result = patch_engine.apply_patches(schedule['container_id'])
                results.append({
                    'container': container['name'],
                    'schedule_id': schedule['id'],
                    'result': result
                })

            except Exception as e:
                logger.error(f"Error running scheduled patch for {schedule.get('container_id', 'unknown')}: {str(e)}")
                continue

        return results

    def add_schedule(self, container_id, schedule_type='daily', time_of_day='02:00',
                    day_of_week=1, enabled=True, maintenance_window_start=None,
                    maintenance_window_end=None):
        """Add a new schedule"""
        # In a real implementation, this would save to database
        pass

    def update_schedule(self, schedule_id, **kwargs):
        """Update an existing schedule"""
        # In a real implementation, this would update the database
        pass

    def delete_schedule(self, schedule_id):
        """Delete a schedule"""
        # In a real implementation, this would remove from database
        pass

# Global instance for use in the application
scheduler = Scheduler()