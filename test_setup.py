#!/usr/bin/env python3
"""
Test script to verify the patch management system setup
"""
import sys
import os

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_imports():
    """Test that all modules can be imported successfully"""
    try:
        from .app import create_app
        print("✓ App creation function imported successfully")

        from .app.database import get_db, init_db
        print("✓ Database module imported successfully")

        from .app.container_manager import ContainerManager
        print("✓ Container manager imported successfully")

        from .app.patch_engine import PatchEngine
        print("✓ Patch engine imported successfully")

        from .app.scheduler import Scheduler
        print("✓ Scheduler imported successfully")

        from .app.alerting import AlertingSystem
        print("✓ Alerting system imported successfully")

        from .app.cli import main as cli_main
        print("✓ CLI module imported successfully")

        print("\nAll modules imported successfully!")
        return True

    except Exception as e:
        print(f"❌ Import test failed: {str(e)}")
        return False

def test_requirements():
    """Test that requirements can be installed"""
    try:
        import flask
        import paramiko
        import sqlalchemy
        import python_crontab
        import requests

        print("✓ All required packages imported successfully")
        return True

    except ImportError as e:
        print(f"❌ Missing requirement: {str(e)}")
        return False

if __name__ == '__main__':
    print("Testing patch management system setup...")
    print("=" * 50)

    success = True
    success &= test_imports()
    success &= test_requirements()

    print("=" * 50)
    if success:
        print("✓ All tests passed! System is ready for use.")
        sys.exit(0)
    else:
        print("❌ Some tests failed!")
        sys.exit(1)