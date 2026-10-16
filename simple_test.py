#!/usr/bin/env python3
"""
Simple test to verify file structure and basic functionality
"""
import os

def check_files():
    """Check that all required files exist"""
    required_files = [
        'README.md',
        'Dockerfile',
        'requirements.txt',
        'app/__init__.py',
        'app/database.py',
        'app/container_manager.py',
        'app/patch_engine.py',
        'app/scheduler.py',
        'app/alerting.py',
        'app/cli.py',
        'app/routes.py',
        'web/templates/index.html'
    ]

    missing_files = []
    for file_path in required_files:
        if not os.path.exists(file_path):
            missing_files.append(file_path)

    if missing_files:
        print("❌ Missing files:")
        for f in missing_files:
            print(f"  {f}")
        return False
    else:
        print("✓ All required files present")
        return True

def check_requirements():
    """Check requirements file"""
    try:
        with open('requirements.txt', 'r') as f:
            content = f.read()

        required_packages = ['Flask', 'paramiko', 'sqlalchemy', 'python-crontab', 'requests']
        missing = []

        for package in required_packages:
            if package not in content:
                missing.append(package)

        if missing:
            print("❌ Missing packages in requirements:")
            for pkg in missing:
                print(f"  {pkg}")
            return False
        else:
            print("✓ All required packages listed in requirements")
            return True

    except Exception as e:
        print(f"❌ Error reading requirements: {str(e)}")
        return False

def check_structure():
    """Check overall project structure"""
    expected_dirs = ['app', 'web/templates']

    for directory in expected_dirs:
        if not os.path.exists(directory):
            print(f"❌ Missing directory: {directory}")
            return False

    print("✓ Project structure is correct")
    return True

if __name__ == '__main__':
    print("Testing patch management system structure...")
    print("=" * 50)

    success = True
    success &= check_files()
    success &= check_requirements()
    success &= check_structure()

    print("=" * 50)
    if success:
        print("✓ All tests passed! System structure is ready.")
    else:
        print("❌ Some tests failed!")