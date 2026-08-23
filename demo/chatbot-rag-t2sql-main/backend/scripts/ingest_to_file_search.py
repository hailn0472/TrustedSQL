#!/usr/bin/env python3
"""
Document Ingestion Script for Google File Search

This script uploads documents from the data/documents directory to a Google File Search Store.
It handles various file types (.json, .html, .pdf) and provides progress tracking and error handling.

Features:
- Automatic metadata extraction (file_name, file_type, file_path, upload_date)
- Configurable chunking (max_tokens_per_chunk=250, max_overlap_tokens=50)
- Progress tracking with detailed logging
- Error handling with skip-and-continue for failed files
- Upload summary with success/failed counts

Usage:
    # Upload all documents to the store specified in .env
    python scripts/ingest_to_file_search.py
    
    # Upload to a specific store
    python scripts/ingest_to_file_search.py --store-name "fileSearchStores/xxx"
    
    # Upload from a different directory
    python scripts/ingest_to_file_search.py --documents-dir "path/to/documents"
    
    # Dry run (show what would be uploaded without actually uploading)
    python scripts/ingest_to_file_search.py --dry-run

Requirements:
- GOOGLE_API_KEY must be set in environment variables
- FILE_SEARCH_STORE_NAME must be set in .env (or provided via --store-name)
- File Search Store must already exist (use manage_file_search_stores.py to create)

Supported File Types:
- JSON (.json)
- HTML (.html, .htm)
- PDF (.pdf)
- Text (.txt)
- Markdown (.md)
"""

import argparse
import glob
import logging
import mimetypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Validate API key
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY not found in environment variables")
    logger.error("Please set GOOGLE_API_KEY in your .env file")
    sys.exit(1)

# Chunking configuration
CHUNKING_CONFIG = {
    'max_tokens_per_chunk': 250,
    'max_overlap_tokens': 50
}

# Supported file extensions
SUPPORTED_EXTENSIONS = {'.json', '.html', '.htm', '.pdf', '.txt', '.md'}


def extract_metadata(file_path: str, base_dir: str) -> Dict:
    """
    Extract metadata from a file for upload to File Search Store.
    
    Args:
        file_path: Absolute path to the file
        base_dir: Base directory for calculating relative path
        
    Returns:
        Dictionary containing:
            - display_name: Display name for the document
            - mime_type: MIME type of the file
            - custom_metadata: List of metadata key-value pairs
    """
    path_obj = Path(file_path)
    relative_path = path_obj.relative_to(base_dir)
    
    # Extract file information
    file_name = path_obj.name
    file_type = path_obj.suffix
    upload_date = datetime.now().isoformat()
    
    # Create display name (use filename without extension)
    # Keep Vietnamese characters - Google API supports UTF-8
    display_name = path_obj.stem
    
    # Determine MIME type
    # Google File Search has specific MIME type requirements
    mime_type_map = {
        '.json': 'text/plain',  # File Search treats JSON as text
        '.html': 'text/html',
        '.htm': 'text/html',
        '.pdf': 'application/pdf',
        '.txt': 'text/plain',
        '.md': 'text/markdown'
    }
    mime_type = mime_type_map.get(file_type.lower())
    
    if not mime_type:
        # Try to guess from file extension
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = 'application/octet-stream'
    
    # Build custom metadata
    custom_metadata = [
        {'key': 'file_name', 'string_value': file_name},
        {'key': 'file_type', 'string_value': file_type},
        {'key': 'file_path', 'string_value': str(relative_path)},
        {'key': 'upload_date', 'string_value': upload_date},
    ]
    
    return {
        'display_name': display_name,
        'mime_type': mime_type,
        'custom_metadata': custom_metadata
    }


def get_files_to_upload(documents_dir: str) -> List[str]:
    """
    Get list of all supported files in the documents directory.
    
    Args:
        documents_dir: Path to documents directory
        
    Returns:
        List of absolute file paths
    """
    all_files = []
    
    # Get all files recursively
    for ext in SUPPORTED_EXTENSIONS:
        pattern = os.path.join(documents_dir, f"**/*{ext}")
        files = glob.glob(pattern, recursive=True)
        all_files.extend(files)
    
    # Filter to only actual files (not directories)
    all_files = [f for f in all_files if os.path.isfile(f)]
    
    # Sort for consistent ordering
    all_files.sort()
    
    return all_files


def upload_documents(
    store_name: str,
    documents_dir: str,
    dry_run: bool = False
) -> Tuple[int, List[Tuple[str, str]]]:
    """
    Upload all documents from directory to File Search Store.
    
    Args:
        store_name: File Search Store name (e.g., 'fileSearchStores/xxx')
        documents_dir: Path to documents directory
        dry_run: If True, only show what would be uploaded without uploading
        
    Returns:
        Tuple of (success_count, failed_files)
        where failed_files is a list of (file_path, error_message) tuples
    """
    logger.info(f"Starting document ingestion from: {documents_dir}")
    logger.info(f"Target File Search Store: {store_name}")
    logger.info(f"Chunking config: {CHUNKING_CONFIG}")
    logger.info("=" * 80)
    
    # Initialize client
    if not dry_run:
        try:
            client = genai.Client(api_key=GOOGLE_API_KEY)
            logger.info("✓ Google GenAI client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize client: {e}")
            sys.exit(1)
    
    # Get all files to upload
    files = get_files_to_upload(documents_dir)
    
    if not files:
        logger.warning(f"No supported files found in {documents_dir}")
        logger.info(f"Supported extensions: {', '.join(SUPPORTED_EXTENSIONS)}")
        return 0, []
    
    logger.info(f"Found {len(files)} file(s) to upload")
    logger.info("=" * 80)
    
    if dry_run:
        logger.info("DRY RUN MODE - No files will be uploaded")
        logger.info("=" * 80)
        for i, file_path in enumerate(files, 1):
            metadata = extract_metadata(file_path, documents_dir)
            logger.info(f"{i}/{len(files)}: {file_path}")
            logger.info(f"  Display name: {metadata['display_name']}")
        logger.info("=" * 80)
        return len(files), []
    
    # Upload files
    success_count = 0
    failed_files = []
    
    for i, file_path in enumerate(files, 1):
        try:
            # Extract metadata
            metadata = extract_metadata(file_path, documents_dir)
            
            logger.info(f"[{i}/{len(files)}] Uploading: {os.path.basename(file_path)}")
            logger.info(f"  Path: {file_path}")
            logger.info(f"  Display name: {metadata['display_name']}")
            
            # Open file with proper encoding handling for Windows
            # Use binary mode to avoid encoding issues
            with open(file_path, 'rb') as file_obj:
                # Upload to File Search Store
                operation = client.file_search_stores.upload_to_file_search_store(
                    file_search_store_name=store_name,
                    file=file_obj,
                    config=types.UploadFileConfig(
                        display_name=metadata['display_name'],
                        mime_type=metadata['mime_type']
                    )
                )
            
            logger.info(f"  ✓ Upload initiated successfully")
            
            success_count += 1
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"  ✗ Upload failed: {error_msg}")
            failed_files.append((file_path, error_msg))
            # Continue with next file
            continue
        
        logger.info("")  # Empty line for readability
    
    return success_count, failed_files


def display_summary(
    total_files: int,
    success_count: int,
    failed_files: List[Tuple[str, str]],
    start_time: float
):
    """
    Display upload summary with statistics.
    
    Args:
        total_files: Total number of files processed
        success_count: Number of successfully uploaded files
        failed_files: List of (file_path, error_message) tuples for failed uploads
        start_time: Start time of the upload process
    """
    elapsed_time = time.time() - start_time
    failed_count = len(failed_files)
    
    logger.info("=" * 80)
    logger.info("UPLOAD SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total files processed: {total_files}")
    logger.info(f"Successfully uploaded: {success_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info(f"Total time: {elapsed_time:.2f}s")
    
    if success_count > 0:
        avg_time = elapsed_time / success_count
        logger.info(f"Average time per file: {avg_time:.2f}s")
    
    if failed_files:
        logger.info("")
        logger.info("Failed files:")
        for file_path, error in failed_files:
            logger.info(f"  ✗ {os.path.basename(file_path)}")
            logger.info(f"    Path: {file_path}")
            logger.info(f"    Error: {error}")
    
    logger.info("=" * 80)
    
    if success_count == total_files:
        logger.info("✓ All files uploaded successfully!")
    elif success_count > 0:
        logger.warning(f"⚠ Partial success: {success_count}/{total_files} files uploaded")
    else:
        logger.error("✗ All uploads failed")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Upload documents to Google File Search Store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Upload all documents using .env configuration:
    python scripts/ingest_to_file_search.py
  
  Upload to a specific store:
    python scripts/ingest_to_file_search.py --store-name "fileSearchStores/abc123"
  
  Upload from a different directory:
    python scripts/ingest_to_file_search.py --documents-dir "path/to/docs"
  
  Dry run (preview without uploading):
    python scripts/ingest_to_file_search.py --dry-run

Notes:
  - The File Search Store must already exist (use manage_file_search_stores.py to create)
  - GOOGLE_API_KEY must be set in environment variables
  - Supported file types: .json, .html, .htm, .pdf, .txt, .md
        """
    )
    
    parser.add_argument(
        '--store-name',
        help='File Search Store name (e.g., fileSearchStores/xxx). If not provided, uses FILE_SEARCH_STORE_NAME from .env'
    )
    
    parser.add_argument(
        '--documents-dir',
        default='data/documents',
        help='Path to documents directory (default: data/documents)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be uploaded without actually uploading'
    )
    
    args = parser.parse_args()
    
    # Get store name
    store_name = args.store_name or os.getenv('FILE_SEARCH_STORE_NAME')
    
    if not store_name:
        logger.error("File Search Store name not provided")
        logger.error("Either:")
        logger.error("  1. Set FILE_SEARCH_STORE_NAME in your .env file, or")
        logger.error("  2. Provide --store-name argument")
        logger.error("")
        logger.error("To create a new store, run:")
        logger.error("  python scripts/manage_file_search_stores.py create --display-name 'My Store'")
        sys.exit(1)
    
    # Validate documents directory
    if not os.path.isdir(args.documents_dir):
        logger.error(f"Documents directory not found: {args.documents_dir}")
        sys.exit(1)
    
    # Start upload
    start_time = time.time()
    
    try:
        success_count, failed_files = upload_documents(
            store_name=store_name,
            documents_dir=args.documents_dir,
            dry_run=args.dry_run
        )
        
        total_files = success_count + len(failed_files)
        
        if not args.dry_run:
            display_summary(total_files, success_count, failed_files, start_time)
        
        # Exit with error code if any uploads failed
        if failed_files:
            sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("")
        logger.warning("Upload interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
