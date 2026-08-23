"""
Flask Application Entry Point
Main entry point for running the Smart Chatbot Flask application
"""
import logging
import sys
import os

# Add parent directory to path to import from app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app

# Configure logging for main entry point
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log')
    ]
)

logger = logging.getLogger(__name__)

# Create Flask app instance
try:
    app = create_app()
    logger.info("Flask application instance created successfully")
except Exception as e:
    logger.error(f"Failed to create Flask application: {e}")
    sys.exit(1)


if __name__ == '__main__':
    """
    Run the Flask development server
    
    This block runs when the script is executed directly.
    For production, use a WSGI server like Gunicorn:
        gunicorn -w 4 -b 0.0.0.0:5000 app.main:app
    """
    logger.info("=" * 60)
    logger.info("Starting Flask development server...")
    logger.info("=" * 60)
    
    # Get configuration from environment or use defaults
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    
    logger.info(f"Server configuration:")
    logger.info(f"  - Host: {host}")
    logger.info(f"  - Port: {port}")
    logger.info(f"  - Debug: {debug}")
    logger.info("=" * 60)
    logger.info(f"Server will be available at: http://{host}:{port}")
    logger.info("Press CTRL+C to stop the server")
    logger.info("=" * 60)
    
    try:
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=debug  # Auto-reload on code changes in debug mode
        )
    except KeyboardInterrupt:
        logger.info("\nServer stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)
