"""
Main entry point for the patch management system
"""
import os
import sys
from . import create_app
from .routes import main

def main_entry():
    """Main entry point for the application"""

    # Create the Flask app
    app = create_app()

    # Register routes
    app.register_blueprint(main)

    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))

    # Run the application
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main_entry()