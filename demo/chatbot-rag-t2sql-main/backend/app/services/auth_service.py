"""
Authentication Service
Handles user registration, authentication, and validation
"""
import random
import string
import re
from datetime import datetime, date
from typing import Optional


class DuplicateUserError(Exception):
    """Raised when attempting to register a username that already exists"""
    pass


class AuthenticationError(Exception):
    """Raised when authentication fails"""
    pass


class UserNotFoundError(Exception):
    """Raised when user is not found"""
    pass


class AuthService:
    """Service for handling authentication operations"""
    
    def __init__(self, sql_service):
        """
        Initialize authentication service
        
        Args:
            sql_service: SQL service instance for database operations
        """
        self.sql_service = sql_service
    
    def generate_user_id(self) -> str:
        """
        Generate a unique 10-character user ID.
        Format: U + 9 digits (e.g., U000000001)
        
        Returns:
            String user_id
        """
        # Generate random 9-digit number
        random_digits = ''.join(random.choices(string.digits, k=9))
        user_id = f"U{random_digits}"
        return user_id
    
    def hash_password(self, password: str) -> str:
        """
        Store password (plain text as requested by user).
        Note: This is NOT secure. Password hashing should be used in production.
        
        Args:
            password: Plain text password
        
        Returns:
            Password string (plain text)
        """
        # User requested no password hashing
        return password
    
    def verify_password(self, password: str, stored_password: str) -> bool:
        """
        Verify password against stored password.
        
        Args:
            password: Plain text password to verify
            stored_password: Stored password
        
        Returns:
            True if password matches, False otherwise
        """
        # Simple comparison since no hashing
        return password == stored_password
    
    def validate_registration_data(self, user_data: dict) -> None:
        """
        Validate all registration fields.
        
        Args:
            user_data: Dictionary containing registration data
        
        Raises:
            ValueError: If any validation fails
        """
        # Validate username
        username = user_data.get('username', '')
        if not username or len(username) < 3 or len(username) > 50:
            raise ValueError("Username must be between 3 and 50 characters")
        
        if not re.match(r'^[a-zA-Z0-9_-]+$', username):
            raise ValueError("Username can only contain alphanumeric characters, underscores, and hyphens")
        
        # Validate password
        password = user_data.get('password', '')
        if not password or len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")
        
        # Validate fullname
        fullname = user_data.get('fullname', '')
        if not fullname or len(fullname) < 1 or len(fullname) > 50:
            raise ValueError("Full name must be between 1 and 50 characters")
        
        # Validate gender
        user_gender = user_data.get('user_gender', '')
        valid_genders = ['Male', 'Female', 'Other']
        if user_gender not in valid_genders:
            raise ValueError(f"Gender must be one of: {', '.join(valid_genders)}")
        
        # Validate date of birth
        user_dob = user_data.get('user_dob', '')
        if not user_dob:
            raise ValueError("Date of birth is required")
        
        try:
            # Parse date
            if isinstance(user_dob, str):
                dob_date = datetime.strptime(user_dob, '%Y-%m-%d').date()
            elif isinstance(user_dob, date):
                dob_date = user_dob
            else:
                raise ValueError("Invalid date format")
            
            # Check age (must be at least 13 years old)
            today = date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            
            if age < 13:
                raise ValueError("User must be at least 13 years old")
                
        except ValueError as e:
            if "does not match format" in str(e) or "Invalid date format" in str(e):
                raise ValueError("Date of birth must be in YYYY-MM-DD format")
            raise
        
        # Validate address
        user_address = user_data.get('user_address', '')
        if not user_address or len(user_address) < 1 or len(user_address) > 150:
            raise ValueError("Address must be between 1 and 150 characters")
    
    def register_user(self, user_data: dict) -> dict:
        """
        Register a new user with validation and password storage.
        
        Args:
            user_data: Dictionary containing username, password, fullname,
                       user_gender, user_dob, user_address
        
        Returns:
            Dictionary with user_id and username
        
        Raises:
            ValueError: If validation fails
            DuplicateUserError: If username already exists
        """
        # Validate all fields
        self.validate_registration_data(user_data)
        
        # Check if username already exists
        if self.sql_service.username_exists(user_data['username']):
            raise DuplicateUserError("Username already exists")
        
        # Generate unique user ID
        user_id = self.generate_user_id()
        
        # Hash password (currently just stores plain text as requested)
        hashed_password = self.hash_password(user_data['password'])
        
        # Prepare user data for database
        db_user_data = {
            'user_id': user_id,
            'username': user_data['username'],
            'password': hashed_password,
            'fullname': user_data['fullname'],
            'user_gender': user_data['user_gender'],
            'user_dob': user_data['user_dob'],
            'user_address': user_data['user_address']
        }
        
        # Create user in database
        created_user_id = self.sql_service.create_user(db_user_data)
        
        return {
            'user_id': created_user_id,
            'username': user_data['username']
        }
    
    def authenticate_user(self, username: str, password: str) -> dict:
        """
        Authenticate user credentials.
        
        Args:
            username: User's username
            password: User's plain text password
        
        Returns:
            Dictionary with user profile data (excluding password)
        
        Raises:
            AuthenticationError: If credentials are invalid
        """
        # Get user by username
        user = self.sql_service.get_user_by_username(username)
        
        if not user:
            raise AuthenticationError("Invalid credentials")
        
        # Verify password
        if not self.verify_password(password, user['password']):
            raise AuthenticationError("Invalid credentials")
        
        # Return user profile without password
        return {
            'user_id': user['user_id'],
            'username': user['username'],
            'fullname': user['fullname'],
            'user_gender': user['user_gender'],
            'user_dob': user['user_dob'],
            'user_address': user['user_address']
        }
    
    def get_user_by_id(self, user_id: str) -> dict:
        """
        Retrieve user profile by user_id.
        
        Args:
            user_id: Unique user identifier
        
        Returns:
            Dictionary with user profile data (excluding password)
        
        Raises:
            UserNotFoundError: If user doesn't exist
        """
        user = self.sql_service.get_user_by_id(user_id)
        
        if not user:
            raise UserNotFoundError(f"User with ID {user_id} not found")
        
        # Return user profile without password
        return {
            'user_id': user['user_id'],
            'username': user['username'],
            'fullname': user['fullname'],
            'user_gender': user['user_gender'],
            'user_dob': user['user_dob'],
            'user_address': user['user_address']
        }
