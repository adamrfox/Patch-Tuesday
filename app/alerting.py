"""
Alerting system for patch operations
"""
import logging
import requests
from .database import get_configuration

logger = logging.getLogger(__name__)

class AlertingSystem:
    """Handles notifications for patch operations"""

    def __init__(self):
        pass

    def send_ntfy_alert(self, message, title=None, priority='normal', tags=None):
        """Send alert via ntfy service"""
        try:
            ntfy_url = get_configuration('ntfy_url')
            if not ntfy_url:
                logger.warning("ntfy URL not configured, skipping alert")
                return False

            headers = {
                'Title': title or 'Patch Management Alert',
                'Priority': priority
            }

            if tags:
                headers['Tags'] = ','.join(tags)

            response = requests.post(
                ntfy_url,
                data=message,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                logger.info("ntfy alert sent successfully")
                return True
            else:
                logger.error(f"Failed to send ntfy alert: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending ntfy alert: {str(e)}")
            return False

    def send_smtp_alert(self, subject, message, recipients):
        """Send alert via SMTP"""
        try:
            smtp_host = get_configuration('smtp_host')
            smtp_port = int(get_configuration('smtp_port') or 587)
            smtp_user = get_configuration('smtp_user')
            smtp_password = get_configuration('smtp_password')

            if not all([smtp_host, smtp_user, smtp_password]):
                logger.warning("SMTP configuration incomplete, skipping alert")
                return False

            # In a real implementation, this would use smtplib to send email
            # This is just a placeholder for the concept
            logger.info(f"Would send SMTP alert to {recipients}: {subject}")
            return True

        except Exception as e:
            logger.error(f"Error sending SMTP alert: {str(e)}")
            return False

    def send_update_notification(self, container_name, status, packages_updated=None,
                               error_message=None):
        """Send notification about an update operation"""
        if status == 'success':
            title = f"✅ Patch Update Successful: {container_name}"
            message = f"Successfully updated {container_name}\n"
            if packages_updated:
                message += f"Packages updated: {packages_updated}\n"
            message += "No errors encountered."

            tags = ['check', 'success']

        elif status == 'failed':
            title = f"❌ Patch Update Failed: {container_name}"
            message = f"Failed to update {container_name}\n"
            if error_message:
                message += f"Error: {error_message}\n"
            message += "Please check the logs for details."

            tags = ['x', 'error']
        else:
            title = f"⚠️ Patch Update Status: {container_name}"
            message = f"Update status for {container_name}: {status}"
            tags = ['warning']

        # Send via ntfy
        self.send_ntfy_alert(message, title=title, tags=tags)

        # In a real implementation, we might also send via SMTP
        # self.send_smtp_alert(title, message, ['admin@example.com'])

# Global instance for use in the application
alerting_system = AlertingSystem()