"""
Patch Management System for Proxmox LXC Containers

This module initializes the Flask application with proper configuration
and database setup.
"""

import logging
from logging.handlers import RotatingFileHandler
from flask import Flask
import os
from .database import init_db


def _setup_logging(app):
    """Configure file + stdout logging for the application.

    Uses a RotatingFileHandler (10 MB per file, 5 backups) and also
    attaches a StreamHandler so logs survive container restarts via
    ``docker logs`` / other log collectors.
    """
    # Choose log path; fall back to app directory if /var/log is unwritable
    log_dir = os.environ.get('LOG_DIR', '/var/log/patch-tuesday')
    log_path = os.path.join(log_dir, 'app.log')

    os.makedirs(log_dir, exist_ok=True)

    # Formatter used for all handlers
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # File handler — 10 MB per file, keep 5 backups
    fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(fmt)
    app.logger.addHandler(fh)

    # Also add it to the root logger so ``logging.getLogger(__name__)``
    # calls in patch_engine, scheduler, etc. land in the file too.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Avoid adding duplicate handlers on repeated calls (e.g. reloaded dev server)
    if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
        root_logger.addHandler(fh)

    # Console handler for docker-observable logs
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    app.logger.addHandler(ch)
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        root_logger.addHandler(ch)

    app.logger.setLevel(logging.INFO)


def create_app():
    """Create and configure the Flask application"""

    app = Flask(__name__, template_folder='../web/templates', static_folder='../static')

    # Configure the application
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['DATABASE_PATH'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'patch_tuesday.db')

    # Initialise database and logging (order matters — init_db runs before create_app returns)
    init_db(app)
    _setup_logging(app)

    return app