"""
Authentication API Routes
Handles user registration, login, profile, and logout endpoints
"""
import logging
from flask import Blueprint, request, jsonify
from app.services.sql_service import SQLService
from app.services.auth_service import AuthService, DuplicateUserError, AuthenticationError, UserNotFoundError
from app.utils.auth_utils import generate_token, require_auth
from app.settings import DATABASE_URI

logger = logging.getLogger(__name__)

# Create authentication blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# Initialize services
sql_service = SQLService(DATABASE_URI)
auth_service = AuthService(sql_service)

# Session blacklist for logout (in-memory for now)
# In production, use Redis or database
token_blacklist = set()


@auth_bp.route('/register', methods=['POST', 'OPTIONS'])
def register():
    """
    Register a new user
    
    Request Body:
        {
            "username": str,
            "password": str,
            "fullname": str,
            "user_gender": str,
            "user_dob": str (YYYY-MM-DD),
            "user_address": str
        }
    
    Returns:
        201: User registered successfully
        400: Validation error
        409: Username already exists
        500: Server error
    """
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Get request data
        data = request.get_json()
        
        if not data:
            return jsonify({
                'error': 'Validation error',
                'message': 'Request body is required',
                'field': 'body'
            }), 400
        
        # Validate required fields
        required_fields = ['username', 'password', 'fullname', 'user_gender', 'user_dob', 'user_address']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'error': 'Validation error',
                    'message': f'{field} is required',
                    'field': field
                }), 400
        
        # Register user
        result = auth_service.register_user(data)
        
        return jsonify({
            'message': 'User registered successfully',
            'user_id': result['user_id'],
            'username': result['username']
        }), 201
        
    except ValueError as e:
        # Validation error
        return jsonify({
            'error': 'Validation error',
            'message': str(e)
        }), 400
        
    except DuplicateUserError as e:
        # Username already exists
        return jsonify({
            'error': 'Conflict',
            'message': str(e)
        }), 409
        
    except Exception as e:
        # Server error
        logger.error(f"Registration error: {e}")
        return jsonify({
            'error': 'Server error',
            'message': 'An unexpected error occurred'
        }), 500


@auth_bp.route('/login', methods=['POST', 'OPTIONS'])
def login():
    """
    Login user and generate JWT token
    
    Request Body:
        {
            "username": str,
            "password": str
        }
    
    Returns:
        200: Login successful with token and user profile
        400: Validation error
        401: Authentication failed
        500: Server error
    """
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Get request data
        data = request.get_json()
        
        if not data:
            return jsonify({
                'error': 'Validation error',
                'message': 'Request body is required'
            }), 400
        
        # Validate required fields
        username = data.get('username')
        password = data.get('password')
        
        if not username:
            return jsonify({
                'error': 'Validation error',
                'message': 'username is required',
                'field': 'username'
            }), 400
        
        if not password:
            return jsonify({
                'error': 'Validation error',
                'message': 'password is required',
                'field': 'password'
            }), 400
        
        # Authenticate user
        user_profile = auth_service.authenticate_user(username, password)
        
        # Generate JWT token
        token = generate_token(user_profile['user_id'], user_profile['username'])
        
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': user_profile
        }), 200
        
    except AuthenticationError as e:
        # Authentication failed
        return jsonify({
            'error': 'Authentication failed',
            'message': 'Invalid credentials'
        }), 401
        
    except Exception as e:
        # Server error
        logger.error(f"Login error: {e}")
        return jsonify({
            'error': 'Server error',
            'message': 'An unexpected error occurred'
        }), 500


@auth_bp.route('/profile', methods=['GET', 'OPTIONS'])
def get_profile():
    """
    Get user profile (requires authentication)
    
    Headers:
        Authorization: Bearer <token>
    
    Returns:
        200: User profile
        401: Authentication failed
        404: User not found
        500: Server error
    """
    # Handle OPTIONS request for CORS preflight (before auth check)
    if request.method == 'OPTIONS':
        return '', 204
    
    # Manually apply authentication for GET requests
    from app.utils.auth_utils import verify_token
    
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authentication required', 'message': 'Missing or invalid token'}), 401
    
    token = auth_header.split()[1]
    current_user = verify_token(token)
    if not current_user:
        return jsonify({'error': 'Authentication failed', 'message': 'Invalid or expired token'}), 401
    
    try:
        # Get user profile by ID
        user_profile = auth_service.get_user_by_id(current_user['user_id'])
        
        return jsonify(user_profile), 200
        
    except UserNotFoundError as e:
        return jsonify({
            'error': 'Not found',
            'message': str(e)
        }), 404
        
    except Exception as e:
        # Server error
        logger.error(f"Profile retrieval error: {e}")
        return jsonify({
            'error': 'Server error',
            'message': 'An unexpected error occurred'
        }), 500


@auth_bp.route('/logout', methods=['POST', 'OPTIONS'])
def logout():
    """
    Logout user (invalidate session)
    
    Headers:
        Authorization: Bearer <token>
    
    Returns:
        200: Logout successful
        401: Authentication failed
        500: Server error
    """
    # Handle OPTIONS request for CORS preflight (before auth check)
    if request.method == 'OPTIONS':
        return '', 204
    
    # Manually apply authentication for POST requests
    from app.utils.auth_utils import verify_token
    
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authentication required', 'message': 'Missing or invalid token'}), 401
    
    token = auth_header.split()[1]
    current_user = verify_token(token)
    if not current_user:
        return jsonify({'error': 'Authentication failed', 'message': 'Invalid or expired token'}), 401
    
    try:
        # Get token from header
        auth_header = request.headers.get('Authorization')
        if auth_header:
            token = auth_header.split()[1]
            # Add token to blacklist
            token_blacklist.add(token)
        
        return jsonify({
            'message': 'Logout successful'
        }), 200
        
    except Exception as e:
        # Server error
        logger.error(f"Logout error: {e}")
        return jsonify({
            'error': 'Server error',
            'message': 'An unexpected error occurred'
        }), 500
