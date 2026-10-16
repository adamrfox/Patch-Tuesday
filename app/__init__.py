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

    app = Flask(__name__)

    # Configure the application
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Initialize database
    init_db()

    return app