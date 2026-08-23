"""
JWT Authentication Utilities
Provides token generation, validation, and authentication decorators
"""
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
from app.settings import JWT_SECRET_KEY, JWT_EXPIRATION_HOURS


class TokenExpiredError(Exception):
    """Raised when JWT token has expired"""
    pass


class InvalidTokenError(Exception):
    """Raised when JWT token is invalid"""
    pass


def generate_token(user_id: str, username: str) -> str:
    """
    Generate JWT token with 24-hour expiration.
    
    Args:
        user_id: User's unique identifier
        username: User's username
    
    Returns:
        JWT token string
    """
    now = datetime.utcnow()
    expiration = now + timedelta(hours=JWT_EXPIRATION_HOURS)
    
    payload = {
        'user_id': user_id,
        'username': username,
        'exp': expiration,
        'iat': now
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm='HS256')
    return token


def decode_token(token: str) -> dict:
    """
    Decode and validate JWT token.
    
    Args:
        token: JWT token string
    
    Returns:
        Dictionary with user_id and username
    
    Raises:
        TokenExpiredError: If token has expired
        InvalidTokenError: If token is invalid
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        return {
            'user_id': payload.get('user_id'),
            'username': payload.get('username')
        }
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise InvalidTokenError(f"Invalid token: {str(e)}")


def require_auth(f):
    """
    Decorator to protect routes requiring authentication.
    Extracts token from Authorization header and validates it.
    
    Usage:
        @app.route('/protected')
        @require_auth
        def protected_route(current_user):
            return {'user': current_user}
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({
                'error': 'Authentication failed',
                'message': 'Missing authorization header'
            }), 401
        
        # Extract token from "Bearer <token>" format
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return jsonify({
                'error': 'Authentication failed',
                'message': 'Invalid authorization header format'
            }), 401
        
        token = parts[1]
        
        try:
            # Decode and validate token
            current_user = decode_token(token)
            # Pass current_user to the route function
            return f(current_user, *args, **kwargs)
        except TokenExpiredError:
            return jsonify({
                'error': 'Authentication failed',
                'message': 'Token has expired'
            }), 401
        except InvalidTokenError as e:
            return jsonify({
                'error': 'Authentication failed',
                'message': str(e)
            }), 401
    
    return decorated_function
