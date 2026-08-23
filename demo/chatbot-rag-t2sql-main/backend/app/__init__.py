"""
Flask App Factory
Creates and configures the Flask application with CORS and LangGraph agent
"""
import logging
from flask import Flask
from flask_cors import CORS

from langgraph.checkpoint.sqlite import SqliteSaver

from . import settings
from .chatbot.builder import build_agent

# Configure logging with better console output
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)

# Print startup banner
print("\n" + "=" * 80)
print("🤖 SMART CHATBOT - MULTI-AGENT SYSTEM")
print("=" * 80)


def create_app():
    """
    Flask application factory function.
    Creates and configures the Flask app.
    """
    logger.info("Creating Flask application...")
    
    app = Flask(__name__)
    
    # Load configuration from settings.py
    logger.info("Loading configuration...")
    app.config.from_object(settings)
    logger.info(f"✓ Configuration loaded for FLASK_ENV={settings.FLASK_ENV}")
    
    # Setup CORS
    logger.info("Setting up CORS...")
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:5173", "http://localhost:3000"],  # Vite and React dev servers
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
            "supports_credentials": True,
            "expose_headers": ["Content-Type", "Authorization"],
            "max_age": 3600  # Cache preflight for 1 hour
        }
    })
    logger.info("✓ CORS configured for /api/* endpoints")
    
    # Initialize LangGraph agent in app context
    logger.info("Initializing LangGraph agent...")
    try:
        # Create checkpointer context manager and enter it manually
        # We need to keep it open for the lifetime of the app
        checkpointer_cm = SqliteSaver.from_conn_string(settings.CHECKPOINT_DB_PATH)
        checkpointer = checkpointer_cm.__enter__()
        
        with app.app_context():
            agent = build_agent(checkpointer)
            app.config['AGENT'] = agent
            app.config['CHECKPOINTER_CM'] = checkpointer_cm  # Store context manager for cleanup
            logger.info("✓ LangGraph agent initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize LangGraph agent: {e}")
        raise
    
    # Register blueprints
    logger.info("Registering API blueprints...")
    try:
        from .api.chat_routes import chat_bp
        app.register_blueprint(chat_bp)
        logger.info("✓ Chat routes registered")
    except ImportError as e:
        logger.warning(f"Could not import chat_routes: {e}")
        logger.warning("Chat routes will be registered when implemented")
    
    try:
        from .api.auth_routes import auth_bp
        app.register_blueprint(auth_bp)
        logger.info("✓ Authentication routes registered")
    except ImportError as e:
        logger.warning(f"Could not import auth_routes: {e}")
        logger.warning("Authentication routes will be registered when implemented")
    
    # Register cleanup handler for app shutdown (not per-request)
    import atexit
    
    def cleanup_checkpointer():
        """Close checkpointer connection on app shutdown"""
        checkpointer_cm = app.config.get('CHECKPOINTER_CM')
        if checkpointer_cm:
            try:
                checkpointer_cm.__exit__(None, None, None)
                logger.info("✓ Checkpointer connection closed on shutdown")
            except Exception as e:
                logger.warning(f"Error closing checkpointer: {e}")
    
    # Register cleanup to run when Python exits
    atexit.register(cleanup_checkpointer)
    
    # Health check endpoint
    @app.route('/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "service": "Smart Chatbot API",
            "version": "1.0.0"
        }, 200
    
    # Root endpoint
    @app.route('/', methods=['GET'])
    def root():
        """Root endpoint with API information"""
        return {
            "service": "Smart Chatbot API",
            "version": "1.0.0",
            "endpoints": {
                "health": "/health",
                "chat": "/api/chat"
            }
        }, 200
    
    print("\n" + "=" * 80)
    print("✅ BACKEND READY!")
    print("=" * 80)
    print(f"📊 Configuration:")
    print(f"   • Debug mode: {app.config['DEBUG']}")
    print(f"   • LLM model: {app.config['LLM_MODEL']}")
    print(f"   • File Search Store: {settings.FILE_SEARCH_STORE_NAME}")
    print(f"   • Database: {settings.DATABASE_URI.split('@')[1] if '@' in settings.DATABASE_URI else 'configured'}")
    print("\n🌐 Endpoints:")
    print("   • Health: http://localhost:5000/health")
    print("   • Chat: http://localhost:5000/api/chat")
    print("   • Architecture: http://localhost:5000/api/architecture")
    print("\n📝 Logging:")
    print("   • Console: Real-time logs will appear below")
    print("   • File: backend/app.log")
    print("=" * 80)
    print("\n🎯 Waiting for requests...\n")
    
    return app
