# Backend - Chatbot RAG + Text-to-SQL

This directory contains the backend application for the chatbot. It's built with Python, FastAPI, and LangChain (using LangGraph) to create an intelligent agent that can handle both RAG and Text-to-SQL tasks.

## ⚙️ Tech Stack

- **Web Framework**: Flask
- **Agent Orchestration**: LangGraph
- **AI Framework**: LangChain
- **LLM**: Google Gemini API
- **Document Search**: Google File Search API
- **Database**: SQL Server (via pyodbc)
- **Dependency Management**: pip

## 📋 Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.10+**
- **SQL Server**: An accessible instance of SQL Server (local or remote).
- **ODBC Driver 17 for SQL Server**: [Download here](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).
- **Google Gemini API Key**: [Get one from Google AI Studio](https://makersuite.google.com/app/apikey).

## 🚀 Setup and Installation

1.  **Navigate to the backend directory:**

    ```bash
    cd backend
    ```

2.  **Create and activate a virtual environment:**

    ```bash
    # Tạo virtual environment với Python=3.11
    conda create -p venv python=3.11
    
    # Activate virtual environment
    #Anaconda
    conda activate absolute_path\venv
    ```

3.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up environment variables:**

    Create a `.env` file in the `backend` directory by copying the example file:

    ```bash
    # On Windows
    copy .env.example .env

    # On macOS/Linux
    cp .env.example .env
    ```

    Now, open the `.env` file and fill in your credentials. See the **Environment Variables** section below for details.

## 🗄️ Database Setup

The application requires a SQL Server database. The setup script will create the necessary tables and populate them with sample data.

1.  **Ensure your SQL Server is running.**
2.  **Update the `DATABASE_URI`** in your `.env` file to point to your database.
3.  **Run the setup script:**

    ```bash
    python scripts/setup_database.py
    ```

    This will create tables like `products`, `customers`, `orders`, etc., and insert sample records.

## 📚 Google File Search Store Setup

The application uses Google File Search API for document retrieval. This provides powerful semantic search capabilities without maintaining a local vector store.

### Creating a File Search Store

1.  **Create a new File Search Store:**

    ```bash
    python scripts/manage_file_search_stores.py create --display-name "My Documents Store"
    ```

    This will output the store name (e.g., `fileSearchStores/abc123`). Copy this value.

2.  **Update your `.env` file** with the store name:

    ```
    FILE_SEARCH_STORE_NAME=fileSearchStores/abc123
    ```

### Managing File Search Stores

The management script provides several commands:

```bash
# List all File Search Stores
python scripts/manage_file_search_stores.py list

# Get statistics about a store
python scripts/manage_file_search_stores.py stats --store-name fileSearchStores/abc123

# List documents in a store
python scripts/manage_file_search_stores.py list-docs --store-name fileSearchStores/abc123

# Delete a store (use with caution)
python scripts/manage_file_search_stores.py delete --store-name fileSearchStores/abc123
```

### Uploading Documents

1.  **Place your documents** (e.g., `.json`, `.html`, `.pdf`) inside the `data/documents/` directory.

2.  **Run the ingestion script:**

    ```bash
    python scripts/ingest_to_file_search.py
    ```

    This script will:
    - Upload all documents from `data/documents/` to your File Search Store
    - Automatically chunk documents (250 tokens per chunk, 50 token overlap)
    - Add metadata (file name, type, path) to each document
    - Display progress and summary of successful/failed uploads

3.  **Verify the upload:**

    ```bash
    python scripts/manage_file_search_stores.py list-docs --store-name fileSearchStores/abc123
    ```

### How File Search Works

When a user asks a question:

1. The query is sent to Google File Search API
2. File Search performs semantic search across all uploaded documents
3. Relevant chunks are retrieved with citations
4. The chatbot uses this context to generate an accurate response
5. Citations are included in the response for transparency

## ▶️ Running the Backend Server

Once the setup is complete, you can start the FastAPI server:

```bash
python -m app.main
```

The server will start on `http://localhost:5000` by default. You can access the auto-generated API documentation at `http://localhost:5000/docs`.

## 📝 Environment Variables

These variables must be set in the `backend/.env` file.

| Variable                 | Description                                                                                             | Example                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `GOOGLE_API_KEY`         | **Required.** Your API key for the Google Gemini models.                                                  | `AIzaSy...`                                                                                         |
| `FILE_SEARCH_STORE_NAME` | **Required.** The name of your Google File Search Store.                                                  | `fileSearchStores/abc123`                                                                           |
| `DATABASE_URI`           | **Required.** The connection string for your SQL Server database.                                         | `mssql+pyodbc://user:pass@localhost/dbname?driver=ODBC+Driver+17+for+SQL+Server`                    |
| `FILE_SEARCH_STORE_NAME` | **Required.** The ID of your Google File Search Store for document retrieval.                            | `fileSearchStores/abc123`                                                                           |
| `LLM_MODEL`              | The main Gemini model for the agent.                                                                    | `gemini-2.5-flash` (default)                                                                        |
| `LLM_SQL_MODEL`          | A potentially more powerful model for Text-to-SQL tasks.                                                | `gemini-2.5-flash` (default)                                                                          |
| `LLM_TEMPERATURE`        | The creativity of the LLM (0.0 to 1.0).                                                                 | `0.1e-3` (default)                                                                                     |
| `CHECKPOINT_DB_PATH`     | The path to the SQLite database for storing conversation history.                                       | `data/checkpoints.db` (default)                                                                     |
| `APP_TITLE`              | The title of the Flask application.                                                                   | `Chatbot RAG+SQL API` (default)                                                                     |
| `APP_DESCRIPTION`        | The description for the API documentation.                                                              | `API for the advanced RAG + Text-to-SQL chatbot` (default)                                          |

### SQL Server Connection String

Your `DATABASE_URI` must be properly formatted. If your password contains special characters, ensure they are URL-encoded.

- **Format**: `mssql+pyodbc://<user>:<password>@<host>:<port>/<database>?driver=<driver_name>`
- **Example**: `mssql+pyodbc://sa:YourStrong%40Password@localhost:1433/chatbot_db?driver=ODBC+Driver+17+for+SQL+Server`

## 🔄 ChromaDB vs. Google File Search

This application has migrated from ChromaDB to Google File Search API. Here are the key differences:

### ChromaDB (Previous Approach)

- **Local vector store**: Documents stored on your machine
- **Manual embedding**: Required explicit embedding generation
- **Maintenance**: Need to manage vector store files and updates
- **Dependencies**: Required ChromaDB and LangChain libraries
- **Scaling**: Limited by local storage and memory

### Google File Search (Current Approach)

- **Cloud-based**: Documents stored in Google's infrastructure
- **Automatic indexing**: Google handles chunking and embedding automatically
- **Managed service**: No local maintenance required
- **Simpler dependencies**: Direct Google GenAI SDK integration
- **Scalable**: Handles large document collections efficiently
- **Built-in citations**: Automatic grounding metadata with source references

### Migration Benefits

1. **Reduced complexity**: No need to manage local vector stores
2. **Better search quality**: Leverages Google's advanced semantic search
3. **Automatic updates**: Documents can be updated without rebuilding indexes
4. **Citations included**: Responses automatically include source references
5. **Lower maintenance**: No local storage management required

### When to Use Each

- **Use File Search** when you want managed infrastructure, automatic indexing, and built-in citations
- **Use ChromaDB** when you need complete data control, offline operation, or have strict data privacy requirements

## 📖 Usage Examples

### Example 1: Setting Up a New File Search Store

```bash
# Create a store
python scripts/manage_file_search_stores.py create --display-name "Product Documentation"

# Output: Created File Search Store: fileSearchStores/xyz789

# Add to .env
echo FILE_SEARCH_STORE_NAME=fileSearchStores/xyz789 >> .env

# Upload documents
python scripts/ingest_to_file_search.py

# Verify
python scripts/manage_file_search_stores.py stats --store-name fileSearchStores/xyz789
```

### Example 2: Querying Documents

Once your server is running and documents are uploaded, the chatbot will automatically use File Search when answering questions:

```
User: "What are the features of product X?"

Agent: Based on the documentation, product X includes:
- Feature A: [description]
- Feature B: [description]

Citations:
1. product_x_specs.pdf
2. product_catalog.json
```

### Example 3: Managing Multiple Stores

```bash
# List all stores
python scripts/manage_file_search_stores.py list

# Switch between stores by updating .env
FILE_SEARCH_STORE_NAME=fileSearchStores/store1  # Development
FILE_SEARCH_STORE_NAME=fileSearchStores/store2  # Production
```
