"""
Patch Management System for Proxmox LXC Containers

This module initializes the Flask application with proper configuration
and database setup.
"""

from flask import Flask
import os
from .database import init_db

def create_app():
    """Create and configure the Flask application"""

    app = Flask(__name__, template_folder='../web/templates')

    # Configure the application
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['DATABASE_PATH'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'patch_manager.db')

    # Initialize database
    init_db(app)

    return app