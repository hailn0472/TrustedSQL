"""
File Search Service for Google File Search API
Handles document retrieval using Google's File Search Store
"""
import logging
import time
from typing import Dict, List, Optional
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class FileSearchStoreNotFoundError(Exception):
    """Raised when the specified File Search Store does not exist"""
    pass


class FileSearchService:
    """
    Service for managing Google File Search Store and document retrieval.
    
    This service replaces the ChromaDB-based RAGService by using Google's
    File Search API for semantic search and document retrieval.
    """
    
    def __init__(self, file_search_store_name: str, model: str = "gemini-2.0-flash-exp"):
        """
        Initialize File Search Service.
        
        Args:
            file_search_store_name: Name of the File Search Store 
                                   (e.g., 'fileSearchStores/xxx')
            model: Gemini model to use for generation (default: gemini-2.0-flash-exp)
            
        Raises:
            ValueError: If file_search_store_name is empty or None
        """
        if not file_search_store_name:
            raise ValueError("file_search_store_name cannot be empty")
            
        self.file_search_store_name = file_search_store_name
        self.model = model
        self.client: Optional[genai.Client] = None
        
        logger.info(
            f"FileSearchService initialized with store: {file_search_store_name}, "
            f"model: {model}"
        )
        
    def initialize_client(self):
        """
        Initialize Google GenAI client.
        
        Uses GOOGLE_API_KEY from environment variables.
        
        Raises:
            AuthenticationError: If API key is invalid or missing
            Exception: For other initialization errors
        """
        try:
            self.client = genai.Client()
            logger.info("Google GenAI client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google GenAI client: {e}")
            raise Exception(
                f"Failed to initialize Google GenAI client. "
                f"Please check your GOOGLE_API_KEY environment variable. Error: {e}"
            )

    def search(self, query: str, k: int = 4) -> Dict:
        """
        Search for relevant documents using File Search API.
        
        Args:
            query: Search query string
            k: Number of results (note: File Search API determines actual count)
            
        Returns:
            Dictionary containing:
                - context (str): Combined text from retrieved chunks
                - citations (List[Dict]): List of citation information with:
                    - title (str): Document title
                    - preview (str): First 200 chars of chunk
                    - chunk_length (int): Length of retrieved chunk
                - grounding_metadata (object): Raw metadata from API response
                
        Raises:
            RuntimeError: If client is not initialized
            FileSearchStoreNotFoundError: If the File Search Store does not exist
            Exception: For other API errors
        """
        if self.client is None:
            error_msg = "Client not initialized. Call initialize_client() first."
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        logger.info(f"Searching File Search Store with query: {query[:100]}...")
        start_time = time.time()
        
        try:
            # Generate content with File Search tool
            response = self.client.models.generate_content(
                model=self.model,
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[
                        types.Tool(
                            file_search=types.FileSearch(
                                file_search_store_names=[self.file_search_store_name]
                            )
                        )
                    ]
                )
            )
            
            elapsed_time = time.time() - start_time
            logger.info(f"File Search API responded in {elapsed_time:.3f}s")
            
            # Process and return results
            return self._process_response(response)
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Check for store not found error
            if 'not found' in error_str or 'does not exist' in error_str:
                error_msg = (
                    f"File Search Store '{self.file_search_store_name}' not found. "
                    f"Please create the store using the management script: "
                    f"python scripts/manage_file_search_stores.py create <display_name>"
                )
                logger.error(error_msg)
                raise FileSearchStoreNotFoundError(error_msg)
            
            # Check for rate limit
            elif 'rate limit' in error_str or 'quota' in error_str:
                logger.warning(f"Rate limit hit, retrying after delay...")
                time.sleep(2)
                # Retry once
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=query,
                        config=types.GenerateContentConfig(
                            tools=[
                                types.Tool(
                                    file_search=types.FileSearch(
                                        file_search_store_names=[self.file_search_store_name]
                                    )
                                )
                            ]
                        )
                    )
                    return self._process_response(response)
                except Exception as retry_error:
                    logger.error(f"Retry failed: {retry_error}")
                    raise Exception(f"File Search API rate limit exceeded: {retry_error}")
            
            # Generic error
            logger.error(f"File Search API error: {e}", exc_info=True)
            raise Exception(f"File Search API error: {e}")

    def _process_response(self, response) -> Dict:
        """
        Process API response to extract context and citations.
        
        Extracts grounding chunks from the response and formats them into
        a structured dictionary with context text and citation information.
        
        Args:
            response: Response object from File Search API
            
        Returns:
            Dictionary containing:
                - context (str): Combined text from all chunks
                - citations (List[Dict]): List of citation info
                - grounding_metadata (object): Raw grounding metadata
        """
        try:
            # Initialize result structure
            result = {
                'context': '',
                'citations': [],
                'grounding_metadata': None
            }
            
            # Check if response has grounding metadata
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.warning("No candidates in response")
                return result
            
            candidate = response.candidates[0]
            
            # Extract grounding metadata
            if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
                grounding_metadata = candidate.grounding_metadata
                result['grounding_metadata'] = grounding_metadata
                
                # Extract grounding chunks
                if hasattr(grounding_metadata, 'grounding_chunks') and grounding_metadata.grounding_chunks:
                    chunks = grounding_metadata.grounding_chunks
                    logger.info(f"Processing {len(chunks)} grounding chunks")
                    
                    # Combine all chunk texts for context
                    context_parts = []
                    
                    for chunk in chunks:
                        # Extract chunk text
                        chunk_text = ''
                        if hasattr(chunk, 'retrieved_context') and chunk.retrieved_context:
                            if hasattr(chunk.retrieved_context, 'text'):
                                chunk_text = chunk.retrieved_context.text
                        
                        if chunk_text:
                            context_parts.append(chunk_text)
                            
                            # Extract title/source information
                            title = 'Unknown Source'
                            if hasattr(chunk.retrieved_context, 'title'):
                                title = chunk.retrieved_context.title
                            elif hasattr(chunk.retrieved_context, 'uri'):
                                title = chunk.retrieved_context.uri
                            
                            # Create citation entry
                            citation = {
                                'title': title,
                                'preview': chunk_text[:200] + ('...' if len(chunk_text) > 200 else ''),
                                'chunk_length': len(chunk_text)
                            }
                            result['citations'].append(citation)
                    
                    # Combine all chunks into context
                    result['context'] = '\n\n'.join(context_parts)
                    logger.info(
                        f"Extracted {len(result['citations'])} citations, "
                        f"total context length: {len(result['context'])} chars"
                    )
                else:
                    logger.warning("No grounding chunks found in metadata")
            else:
                logger.warning("No grounding metadata in response")
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing response: {e}", exc_info=True)
            # Return empty result on error
            return {
                'context': '',
                'citations': [],
                'grounding_metadata': None
            }


# Example usage
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    # Get configuration from environment
    store_name = os.getenv('FILE_SEARCH_STORE_NAME')
    
    if not store_name:
        print("✗ FILE_SEARCH_STORE_NAME not set in environment")
        print("\nPlease set FILE_SEARCH_STORE_NAME in your .env file")
        exit(1)
    
    # Initialize service
    service = FileSearchService(
        file_search_store_name=store_name,
        model="gemini-2.5-flash"
    )
    
    try:
        # Initialize client
        service.initialize_client()
        print("✓ Client initialized successfully")
        
        # Test search
        test_query = "What is the main topic of the documents?"
        print(f"\n🔍 Testing search with query: {test_query}")
        
        result = service.search(test_query)
        
        print(f"\n✓ Search completed:")
        print(f"  - Context length: {len(result['context'])} chars")
        print(f"  - Citations: {len(result['citations'])}")
        
        if result['citations']:
            print("\n📚 Citations:")
            for i, citation in enumerate(result['citations'], 1):
                print(f"  {i}. {citation['title']}")
                print(f"     Preview: {citation['preview'][:100]}...")
        
        if result['context']:
            print(f"\n📄 Context preview:")
            print(f"  {result['context'][:300]}...")
        
    except FileSearchStoreNotFoundError as e:
        print(f"✗ {e}")
    except Exception as e:
        print(f"✗ Error: {e}")
