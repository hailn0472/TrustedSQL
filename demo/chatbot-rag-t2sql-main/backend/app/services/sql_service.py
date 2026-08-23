"""
SQL Service for SQL Server connection and query execution
Handles Text-to-SQL functionality with security validation
"""
import re
import logging
from typing import List, Tuple, Any
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SQLTimeoutError
from langchain_community.utilities import SQLDatabase

logger = logging.getLogger(__name__)


class SQLService:
    """
    Service for managing the SQL Server connection and query execution.
    Implements security validation to prevent SQL injection.
    """
    
    def __init__(self, database_uri: str):
        """
        Initialize SQL Service
        
        Args:
            database_uri: SQL Server connection string
        """
        self.database_uri = database_uri
        self.engine: Engine = None
        self._sql_database = None
        
    def get_engine(self) -> Engine:
        """
        Creates a SQLAlchemy engine for SQL Server.

        Returns:
            A SQLAlchemy Engine instance.
        """
        if self.engine is None:
            try:
                # Create engine with pyodbc driver
                self.engine = create_engine(
                    self.database_uri,
                    echo=False,  # Set to True for SQL query logging
                    pool_pre_ping=True,  # Verify connections before using
                    pool_recycle=3600,  # Recycle connections after 1 hour
                )
                logger.info("SQL Server engine created successfully")
            except Exception as e:
                logger.error(f"Failed to create SQL Server engine: {e}")
                raise
        
        return self.engine
    
    def get_sql_database(self) -> SQLDatabase:
        """
        Creates a LangChain SQLDatabase object.

        Returns:
            A SQLDatabase object for the SQL tools.
        """
        if self._sql_database is None:
            try:
                engine = self.get_engine()
                self._sql_database = SQLDatabase(engine)
                logger.info("LangChain SQLDatabase created successfully")
            except Exception as e:
                logger.error(f"Failed to create SQLDatabase: {e}")
                raise
        
        return self._sql_database
    
    def validate_query(self, query: str) -> bool:
        """
        Validates the SQL query before execution.

        Security checks:
        - Only allows SELECT statements.
        - No DROP, DELETE, UPDATE, INSERT, or ALTER statements.
        - No multiple statements (semicolon check).
        - No xp_cmdshell or system procedures.
        - No EXEC/EXECUTE commands.

        Args:
            query: The SQL query string to validate.

        Returns:
            True if the query is valid and safe.

        Raises:
            ValueError: If the query is invalid or unsafe.
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        # Normalize query for checking
        query_upper = query.upper().strip()
        
        # Check 1: Must start with SELECT
        if not query_upper.startswith('SELECT'):
            raise ValueError("Only SELECT queries are allowed")
        
        # Check 2: No data modification statements
        dangerous_keywords = [
            'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER',
            'CREATE', 'TRUNCATE', 'REPLACE', 'MERGE'
        ]
        
        for keyword in dangerous_keywords:
            # Use word boundaries to avoid false positives
            pattern = r'\b' + keyword + r'\b'
            if re.search(pattern, query_upper):
                raise ValueError(f"Query contains forbidden keyword: {keyword}")
        
        # Check 3: No multiple statements (semicolon check)
        # Allow semicolon only at the very end
        semicolon_count = query.count(';')
        if semicolon_count > 1:
            raise ValueError("Multiple statements are not allowed")
        if semicolon_count == 1 and not query.rstrip().endswith(';'):
            raise ValueError("Semicolon only allowed at end of query")
        
        # Check 4: No system procedures or xp_cmdshell
        dangerous_procedures = [
            'XP_CMDSHELL', 'SP_EXECUTESQL', 'EXEC', 'EXECUTE',
            'XP_', 'SP_OA'  # Catch xp_* and sp_oa* procedures
        ]
        
        for proc in dangerous_procedures:
            if proc in query_upper:
                raise ValueError(f"Query contains forbidden procedure: {proc}")
        
        # Check 5: No comments that might hide malicious code
        if '--' in query or '/*' in query or '*/' in query:
            raise ValueError("Comments are not allowed in queries")
        
        logger.info("Query validation passed")
        return True
    
    def execute_query(
        self, 
        query: str, 
        timeout: int = 30
    ) -> List[Tuple[Any, ...]]:
        """
        Executes a SQL query on the SQL Server with a timeout and error handling.

        Args:
            query: The SQL query to execute.
            timeout: The query timeout in seconds (default: 30).

        Returns:
            A list of result rows as tuples.

        Raises:
            ValueError: If query validation fails.
            TimeoutError: If the query exceeds the timeout.
            Exception: For other database errors.
        """
        # Validate query first
        self.validate_query(query)
        
        try:
            engine = self.get_engine()
            
            # Execute query with timeout
            with engine.connect() as conn:
                # Set query timeout
                result = conn.execute(
                    text(query),
                    execution_options={"timeout": timeout}
                )
                
                # Fetch all results
                rows = result.fetchall()
                
                logger.info(f"Query executed successfully, returned {len(rows)} rows")
                return rows
                
        except SQLTimeoutError:
            error_msg = f"Query timeout after {timeout} seconds - please try a simpler query"
            logger.error(error_msg)
            raise TimeoutError(error_msg)
            
        except SQLAlchemyError as e:
            error_msg = f"SQL execution error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
            
        except Exception as e:
            error_msg = f"Unexpected error during query execution: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
    
    def close(self):
        """Close database connections"""
        if self.engine:
            self.engine.dispose()
            logger.info("SQL Server connections closed")
    
    # User Management Methods
    
    def create_user(self, user_data: dict) -> str:
        """
        Insert new user into Users table.
        
        Args:
            user_data: Dictionary with all user fields including hashed password
        
        Returns:
            user_id of created user
        
        Raises:
            Exception: If username already exists or insertion fails
        """
        try:
            engine = self.get_engine()
            
            # Check if username exists
            if self.username_exists(user_data['username']):
                raise Exception("Username already exists")
            
            # Insert user
            insert_query = text("""
                INSERT INTO Users (user_id, username, password, fullname, user_gender, user_dob, user_address)
                VALUES (:user_id, :username, :password, :fullname, :user_gender, :user_dob, :user_address)
            """)
            
            with engine.connect() as conn:
                conn.execute(insert_query, user_data)
                conn.commit()
            
            logger.info(f"User created successfully: {user_data['username']}")
            return user_data['user_id']
            
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            raise
    
    def get_user_by_username(self, username: str) -> dict:
        """
        Retrieve user by username.
        
        Args:
            username: Username to search for
        
        Returns:
            Dictionary with all user fields including password hash
            None if user not found
        """
        try:
            engine = self.get_engine()
            
            query = text("""
                SELECT user_id, username, password, fullname, user_gender, user_dob, user_address
                FROM Users
                WHERE username = :username
            """)
            
            with engine.connect() as conn:
                result = conn.execute(query, {'username': username})
                row = result.fetchone()
                
                if row:
                    return {
                        'user_id': row[0],
                        'username': row[1],
                        'password': row[2],
                        'fullname': row[3],
                        'user_gender': row[4],
                        'user_dob': str(row[5]) if row[5] else None,
                        'user_address': row[6]
                    }
                return None
                
        except Exception as e:
            logger.error(f"Failed to get user by username: {e}")
            raise
    
    def get_user_by_id(self, user_id: str) -> dict:
        """
        Retrieve user by user_id.
        
        Args:
            user_id: User ID to search for
        
        Returns:
            Dictionary with all user fields excluding password
            None if user not found
        """
        try:
            engine = self.get_engine()
            
            query = text("""
                SELECT user_id, username, fullname, user_gender, user_dob, user_address
                FROM Users
                WHERE user_id = :user_id
            """)
            
            with engine.connect() as conn:
                result = conn.execute(query, {'user_id': user_id})
                row = result.fetchone()
                
                if row:
                    return {
                        'user_id': row[0],
                        'username': row[1],
                        'fullname': row[2],
                        'user_gender': row[3],
                        'user_dob': str(row[4]) if row[4] else None,
                        'user_address': row[5]
                    }
                return None
                
        except Exception as e:
            logger.error(f"Failed to get user by ID: {e}")
            raise
    
    def username_exists(self, username: str) -> bool:
        """
        Check if username already exists.
        
        Args:
            username: Username to check
        
        Returns:
            True if username exists, False otherwise
        """
        try:
            engine = self.get_engine()
            
            query = text("""
                SELECT COUNT(*) as count
                FROM Users
                WHERE username = :username
            """)
            
            with engine.connect() as conn:
                result = conn.execute(query, {'username': username})
                row = result.fetchone()
                return row[0] > 0
                
        except Exception as e:
            logger.error(f"Failed to check username existence: {e}")
            raise


# Example usage
if __name__ == "__main__":
    # This is for testing purposes only
    from app import settings
    
    sql_service = SQLService(settings.DATABASE_URI)
    
    # Test query validation
    test_queries = [
        "SELECT * FROM products",  # Valid
        "SELECT name, price FROM products WHERE price > 100",  # Valid
        "DROP TABLE products",  # Invalid - DROP
        "SELECT * FROM products; DELETE FROM orders",  # Invalid - multiple statements
        "SELECT * FROM products; --comment",  # Invalid - comment
        "EXEC xp_cmdshell 'dir'",  # Invalid - system procedure
    ]
    
    for query in test_queries:
        try:
            sql_service.validate_query(query)
            print(f"✓ Valid: {query[:50]}...")
        except ValueError as e:
            print(f"✗ Invalid: {query[:50]}... - {e}")
