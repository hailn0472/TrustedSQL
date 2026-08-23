#!/usr/bin/env python3
"""
File Search Store Management Script

This script provides CLI commands to manage Google File Search Stores:
- list: List all File Search Stores
- create: Create a new File Search Store
- delete: Delete a File Search Store
- list-docs: List documents in a store
- stats: Display statistics about a store

Usage:
    python scripts/manage_file_search_stores.py list
    python scripts/manage_file_search_stores.py create --display-name "My Store"
    python scripts/manage_file_search_stores.py delete --store-name "fileSearchStores/xxx"
    python scripts/manage_file_search_stores.py list-docs --store-name "fileSearchStores/xxx"
    python scripts/manage_file_search_stores.py stats --store-name "fileSearchStores/xxx"
"""

import argparse
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

# Validate API key
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY not found in environment variables")
    print("Please set GOOGLE_API_KEY in your .env file")
    sys.exit(1)


def list_stores():
    """List all File Search Stores"""
    print("Listing all File Search Stores...")
    print("=" * 80)
    
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        stores = client.file_search_stores.list()
        
        store_list = list(stores)
        
        if not store_list:
            print("No File Search Stores found.")
            return
        
        print(f"Found {len(store_list)} store(s):\n")
        
        for i, store in enumerate(store_list, 1):
            print(f"{i}. Name: {store.name}")
            print(f"   Display Name: {store.display_name}")
            print(f"   Create Time: {store.create_time}")
            print(f"   Update Time: {store.update_time}")
            print()
        
        print("=" * 80)
        print(f"Total: {len(store_list)} store(s)")
        
    except Exception as e:
        print(f"Error listing stores: {e}")
        sys.exit(1)


def create_store(display_name: str):
    """Create a new File Search Store"""
    print(f"Creating File Search Store: '{display_name}'...")
    print("=" * 80)
    
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        store = client.file_search_stores.create(
            config=types.CreateFileSearchStoreConfig(
                display_name=display_name
            )
        )
        
        print("✓ File Search Store created successfully!")
        print()
        print(f"Store Name: {store.name}")
        print(f"Display Name: {store.display_name}")
        print(f"Create Time: {store.create_time}")
        print()
        print("=" * 80)
        print("Next steps:")
        print(f"1. Add this to your .env file:")
        print(f"   FILE_SEARCH_STORE_NAME={store.name}")
        print(f"2. Upload documents using the ingestion script:")
        print(f"   python scripts/ingest_to_file_search.py")
        
        return store.name
        
    except Exception as e:
        print(f"Error creating store: {e}")
        sys.exit(1)


def delete_store(store_name: str, force: bool = False):
    """Delete a File Search Store"""
    print(f"Deleting File Search Store: '{store_name}'...")
    
    if not force:
        print()
        print("WARNING: This will permanently delete the store and all its documents!")
        confirmation = input("Type 'yes' to confirm deletion: ")
        if confirmation.lower() != 'yes':
            print("Deletion cancelled.")
            return
    
    print("=" * 80)
    
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        client.file_search_stores.delete(name=store_name)
        
        print("✓ File Search Store deleted successfully!")
        print()
        print(f"Deleted: {store_name}")
        print()
        print("=" * 80)
        
    except Exception as e:
        print(f"Error deleting store: {e}")
        sys.exit(1)


def list_documents(store_name: str):
    """List documents in a File Search Store"""
    print(f"Listing documents in store: '{store_name}'...")
    print("=" * 80)
    
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        documents = client.file_search_stores.list_documents(
            file_search_store_name=store_name
        )
        
        doc_list = list(documents)
        
        if not doc_list:
            print("No documents found in this store.")
            return
        
        print(f"Found {len(doc_list)} document(s):\n")
        
        for i, doc in enumerate(doc_list, 1):
            print(f"{i}. Name: {doc.name}")
            print(f"   Display Name: {doc.display_name}")
            print(f"   MIME Type: {doc.mime_type}")
            print(f"   Size: {doc.size_bytes} bytes")
            print(f"   Create Time: {doc.create_time}")
            
            # Display custom metadata if available
            if hasattr(doc, 'custom_metadata') and doc.custom_metadata:
                print(f"   Metadata:")
                for meta in doc.custom_metadata:
                    if hasattr(meta, 'key') and hasattr(meta, 'string_value'):
                        print(f"     - {meta.key}: {meta.string_value}")
            
            print()
        
        print("=" * 80)
        print(f"Total: {len(doc_list)} document(s)")
        
    except Exception as e:
        print(f"Error listing documents: {e}")
        sys.exit(1)


def get_store_stats(store_name: str):
    """Get statistics about a File Search Store"""
    print(f"Getting statistics for store: '{store_name}'...")
    print("=" * 80)
    
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        # Get store info
        store = client.file_search_stores.get(name=store_name)
        
        print("Store Information:")
        print(f"  Name: {store.name}")
        print(f"  Display Name: {store.display_name}")
        print(f"  Create Time: {store.create_time}")
        print(f"  Update Time: {store.update_time}")
        print()
        
        # Get documents
        documents = client.files.list()
        
        doc_list = list(documents)
        
        if not doc_list:
            print("Document Statistics:")
            print("  Total Documents: 0")
            print()
            print("=" * 80)
            return
        
        # Calculate statistics
        total_docs = len(doc_list)
        total_size = sum(doc.size_bytes for doc in doc_list)
        
        # Group by MIME type
        mime_types = {}
        for doc in doc_list:
            mime_type = doc.mime_type
            if mime_type not in mime_types:
                mime_types[mime_type] = 0
            mime_types[mime_type] += 1
        
        print("Document Statistics:")
        print(f"  Total Documents: {total_docs}")
        print(f"  Total Size: {total_size:,} bytes ({total_size / (1024*1024):.2f} MB)")
        print(f"  Average Size: {total_size // total_docs:,} bytes")
        print()
        
        print("Documents by Type:")
        for mime_type, count in sorted(mime_types.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total_docs) * 100
            print(f"  {mime_type}: {count} ({percentage:.1f}%)")
        
        print()
        print("=" * 80)
        
    except Exception as e:
        print(f"Error getting store stats: {e}")
        sys.exit(1)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Manage Google File Search Stores",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  List all stores:
    python scripts/manage_file_search_stores.py list
  
  Create a new store:
    python scripts/manage_file_search_stores.py create --display-name "My Documents"
  
  Delete a store:
    python scripts/manage_file_search_stores.py delete --store-name "fileSearchStores/abc123"
  
  List documents in a store:
    python scripts/manage_file_search_stores.py list-docs --store-name "fileSearchStores/abc123"
  
  Get store statistics:
    python scripts/manage_file_search_stores.py stats --store-name "fileSearchStores/abc123"
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    subparsers.add_parser('list', help='List all File Search Stores')
    
    # Create command
    create_parser = subparsers.add_parser('create', help='Create a new File Search Store')
    create_parser.add_argument(
        '--display-name',
        required=True,
        help='Display name for the new store'
    )
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a File Search Store')
    delete_parser.add_argument(
        '--store-name',
        required=True,
        help='Name of the store to delete (e.g., fileSearchStores/xxx)'
    )
    delete_parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompt'
    )
    
    # List documents command
    list_docs_parser = subparsers.add_parser('list-docs', help='List documents in a store')
    list_docs_parser.add_argument(
        '--store-name',
        required=True,
        help='Name of the store (e.g., fileSearchStores/xxx)'
    )
    
    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Get statistics about a store')
    stats_parser.add_argument(
        '--store-name',
        required=True,
        help='Name of the store (e.g., fileSearchStores/xxx)'
    )
    
    args = parser.parse_args()
    
    # Execute command
    if args.command == 'list':
        list_stores()
    elif args.command == 'create':
        create_store(args.display_name)
    elif args.command == 'delete':
        delete_store(args.store_name, args.force)
    elif args.command == 'list-docs':
        list_documents(args.store_name)
    elif args.command == 'stats':
        get_store_stats(args.store_name)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
