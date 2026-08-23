# Smart Chatbot - RAG + Text-to-SQL System

Hệ thống chatbot thông minh kết hợp RAG (Retrieval-Augmented Generation) và Text-to-SQL với LangGraph routing agent. Chatbot tự động phân tích câu hỏi và quyết định sử dụng RAG để truy xuất thông tin từ documents hoặc Text-to-SQL để query database.

## Tính năng

- 🤖 **Intelligent Routing**: Tự động phân loại câu hỏi và routing giữa RAG, SQL, hoặc General conversation
- 📚 **RAG Pipeline**: Truy xuất thông tin từ dữ liệu phi cấu trúc (PDF, TXT, DOCX, MD)
- 🗄️ **Text-to-SQL**: Chuyển đổi câu hỏi tự nhiên thành SQL queries và thực thi trên SQL Server
- 💬 **Streaming Responses**: Real-time streaming với Server-Sent Events (SSE)
- 🧠 **Conversation Memory**: Lưu trữ và duy trì lịch sử hội thoại với checkpointing
- 🔒 **SQL Security**: Validation và sanitization để ngăn chặn SQL injection
- ⚡ **Modern Stack**: React frontend với Vite, Flask backend với LangGraph
- 🌐 **Multilingual**: Hỗ trợ tiếng Việt và tiếng Anh

## Tech Stack

**Backend:**
- Flask 3.x - Web framework
- LangGraph - Agent orchestration với state machine
- LangChain - RAG và SQL tools
- Google Gemini API - LLM và embeddings
- ChromaDB - Vector store cho document embeddings
- SQL Server - Structured data storage
- pyodbc - SQL Server driver
- Python 3.10+

**Frontend:**
- React 18+ - UI framework
- Vite - Build tool và dev server
- EventSource API - SSE streaming
- Custom hooks - State management

## Prerequisites

Trước khi bắt đầu, đảm bảo bạn đã cài đặt:

- **Python 3.10 hoặc cao hơn**
- **Node.js 18+ và npm**
- **SQL Server** (hoặc SQL Server Express)
- **ODBC Driver 17 for SQL Server** ([Download here](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- **Google Gemini API Key** ([Get it here](https://makersuite.google.com/app/apikey))

## Quick Start

### 1. Clone Repository

```bash
git clone <repository-url>
cd smart-chatbot-rag-sql
```

### 2. Backend Setup

```bash
cd backend

# Tạo virtual environment với Python=3.11
conda create -p venv python=3.11

# Activate virtual environment
#Anaconda
conda activate absolute_path\venv

# Install dependencies
pip install -r requirements.txt

# Tạo file .env từ template
copy .env.example .env  # Windows
# cp .env.example .env  # Linux/Mac

# Cập nhật .env với API keys và database connection (xem phần Environment Variables)
```

### 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Frontend sẽ chạy tại `http://localhost:3000`

### 4. Ingest Documents (Optional)

Nếu bạn muốn sử dụng RAG với documents:

```bash
cd backend

# Đặt documents (PDF, TXT, DOCX, MD) vào thư mục data/documents/
# Sau đó chạy ingestion script:
python scripts/ingest_data.py
```

### 5. Start Backend Server

```bash
cd backend
python -m app.main
```

Backend API sẽ chạy tại `http://localhost:5000`

### 6. Access Application

Mở browser và truy cập `http://localhost:3000` để sử dụng chatbot.

## Environment Variables

Tạo file `backend/.env` với các biến sau (xem `backend/.env.example` để biết chi tiết):

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Google Gemini API key | `AIzaSy...` |
| `DATABASE_URI` | SQL Server connection string | `mssql+pyodbc://user:pass@localhost:1433/dbname?driver=ODBC+Driver+17+for+SQL+Server` |

### Optional Variables (có giá trị mặc định)

| Variable | Description | Default |
|----------|-------------|---------|
| `VECTOR_STORE_PATH` | Đường dẫn lưu ChromaDB vector store | `data/vector_store` |
| `CHROMA_COLLECTION_NAME` | Tên collection trong ChromaDB | `documents` |
| `EMBEDDING_MODEL` | Google embedding model | `models/gemini-embedding-001` |
| `LLM_MODEL` | Google Gemini model | `gemini-2.5-flash` |
| `LLM_TEMPERATURE` | Temperature cho LLM (0.0-1.0) | `0.1e-3` |
| `CHECKPOINT_DB_PATH` | Đường dẫn lưu conversation checkpoints | `data/checkpoints.db` |
| `FLASK_ENV` | Flask environment | `development` |
| `SECRET_KEY` | Flask secret key (đổi trong production!) | `dev-secret-key-change-in-production` |

### SQL Server Connection String Format

```
mssql+pyodbc://username:password@server:port/database?driver=ODBC+Driver+17+for+SQL+Server
```

**Ví dụ:**
- Local SQL Server: `mssql+pyodbc://sa:YourPassword@localhost:1433/chatbot_db?driver=ODBC+Driver+17+for+SQL+Server`
- Remote SQL Server: `mssql+pyodbc://user:pass@192.168.1.100:1433/mydb?driver=ODBC+Driver+17+for+SQL+Server`

**Lưu ý:** Nếu password chứa ký tự đặc biệt, cần URL encode (ví dụ: `@` → `%40`, `#` → `%23`)

## Project Structure

```
smart-chatbot-rag-sql/
├── backend/
│   ├── app/
│   │   ├── __init__.py              # Flask app factory
│   │   ├── main.py                  # Application entry point
│   │   ├── config.py                # Configuration management
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── chat_routes.py       # Chat API endpoints
│   │   ├── chatbot/
│   │   │   ├── __init__.py
│   │   │   ├── state.py             # LangGraph agent state
│   │   │   ├── nodes.py             # LangGraph nodes (route, RAG, SQL, generate)
│   │   │   ├── tools.py             # RAG & SQL tools
│   │   │   └── builder.py           # LangGraph builder
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── rag_service.py       # RAG service (ChromaDB)
│   │       └── sql_service.py       # SQL service (SQL Server)
│   ├── data/
│   │   ├── documents/               # Documents cho RAG (PDF, TXT, DOCX, MD)
│   │   ├── vector_store/            # ChromaDB vector store (auto-generated)
│   │   └── checkpoints.db           # Conversation checkpoints (auto-generated)
│   ├── scripts/
│   │   └── ingest_data.py           # Document ingestion script
│   ├── tests/                       # Unit và integration tests
│   ├── .env                         # Environment variables (không commit!)
│   ├── .env.example                 # Environment variables template
│   └── requirements.txt             # Python dependencies
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── ChatWindow.jsx       # Main chat interface
    │   │   └── Message.jsx          # Message bubble component
    │   ├── hooks/
    │   │   └── useChat.js           # Custom hook cho chat logic
    │   ├── App.jsx                  # Root component
    │   └── main.jsx                 # Entry point
    ├── index.html
    ├── package.json
    └── vite.config.js               # Vite configuration
```

## API Documentation

### POST /api/chat

Gửi message đến chatbot và nhận streaming response.

**Endpoint:** `http://localhost:5000/api/chat`

**Method:** `POST`

**Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "message": "Sản phẩm nào có giá cao nhất?",
  "thread_id": "user-123-session-456"
}
```

**Request Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | Câu hỏi hoặc message từ user |
| `thread_id` | string | Yes | Unique identifier cho conversation thread (dùng để maintain history) |

**Response:**

Server-Sent Events (SSE) stream với các events sau:

**Event: `token`**
```
event: token
data: {"content": "Sản", "done": false}

event: token
data: {"content": " phẩm", "done": false}
```

**Event: `done`**
```
event: done
data: {"done": true}
```

**Event: `error`**
```
event: error
data: {"error": "Error message"}
```

**Example với cURL:**

```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Có bao nhiêu sản phẩm trong database?",
    "thread_id": "test-thread-1"
  }'
```

**Example với JavaScript:**

```javascript
const eventSource = new EventSource(
  'http://localhost:5000/api/chat?' + 
  new URLSearchParams({
    message: 'Sản phẩm nào có giá cao nhất?',
    thread_id: 'user-123'
  })
);

eventSource.addEventListener('token', (event) => {
  const data = JSON.parse(event.data);
  console.log('Token:', data.content);
});

eventSource.addEventListener('done', (event) => {
  console.log('Stream completed');
  eventSource.close();
});

eventSource.addEventListener('error', (event) => {
  console.error('Error:', event);
  eventSource.close();
});
```

**Status Codes:**

| Code | Description |
|------|-------------|
| 200 | Success - streaming response |
| 400 | Bad Request - missing required fields |
| 500 | Internal Server Error |

## Document Ingestion

### Chuẩn bị Documents

1. Tạo thư mục `backend/data/documents/` nếu chưa có
2. Đặt documents vào thư mục này (hỗ trợ: PDF, TXT, DOCX, MD)

### Chạy Ingestion Script

```bash
cd backend
python scripts/ingest_data.py
```

**Script sẽ:**
1. Đọc tất cả documents từ `data/documents/`
2. Chia documents thành chunks (default: 1000 characters, overlap 200)
3. Tạo embeddings sử dụng Google Generative AI
4. Lưu vào ChromaDB vector store tại `data/vector_store/`

**Options:**

```bash
# Custom chunk size và overlap
python scripts/ingest_data.py --chunk-size 1500 --chunk-overlap 300

# Chỉ định thư mục documents
python scripts/ingest_data.py --docs-path /path/to/documents

# Chỉ định vector store path
python scripts/ingest_data.py --vector-store-path /path/to/vector_store
```

**Lưu ý:**
- Ingestion script cần `GOOGLE_API_KEY` trong `.env`
- Quá trình có thể mất vài phút tùy thuộc số lượng documents
- Vector store sẽ được tạo tự động nếu chưa tồn tại
- Chạy lại script sẽ overwrite vector store hiện tại

## Usage Guide

### Sử dụng Chatbot

1. **Mở ứng dụng** tại `http://localhost:3000`
2. **Nhập câu hỏi** vào input box
3. **Nhấn Enter hoặc click Send**
4. **Xem response** streaming real-time

### Các loại câu hỏi

**RAG Queries** (truy xuất từ documents):
- "Chính sách bảo hành của công ty là gì?"
- "Hướng dẫn sử dụng sản phẩm X như thế nào?"
- "Tóm tắt nội dung document về Y"

**SQL Queries** (truy vấn database):
- "Có bao nhiêu sản phẩm trong kho?"
- "Sản phẩm nào có giá cao nhất?"
- "Tổng doanh thu tháng này là bao nhiêu?"
- "Liệt kê 5 khách hàng mua nhiều nhất"

**General Conversation**:
- "Xin chào!"
- "Cảm ơn bạn"
- "Bạn có thể giúp gì cho tôi?"

### Conversation History

Chatbot tự động lưu lịch sử hội thoại theo `thread_id`. Mỗi user session nên có một `thread_id` duy nhất để maintain context.

**Frontend tự động:**
- Generate `thread_id` khi user mở app
- Gửi cùng `thread_id` cho tất cả messages trong session
- Chatbot có thể tham chiếu đến câu hỏi và câu trả lời trước đó

## Development

### Running Tests

```bash
cd backend

# Run all tests
pytest

# Run với coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_services/test_rag_service.py

# Run với verbose output
pytest -v
```

### Code Structure

**LangGraph Flow:**
```
User Message
    ↓
Route Query Node (phân loại: RAG/SQL/General)
    ↓
    ├─→ RAG Node → Generate Response Node
    ├─→ SQL Node → Generate Response Node
    └─→ Generate Response Node (direct)
    ↓
Stream Response to User
```

**Agent State:**
- Sử dụng `AgentState` TypedDict với `messages` field
- Messages được persist với SqliteSaver checkpointer
- Mỗi node có thể đọc và update state

### Debugging

**Enable debug logging:**

```python
# backend/app/main.py
import logging
logging.basicConfig(level=logging.DEBUG)
```

**Check LangGraph execution:**

```python
# Trong nodes.py, thêm logging
import logging
logger = logging.getLogger(__name__)

def route_query(state):
    logger.debug(f"Routing query: {state['messages'][-1].content}")
    # ...
```

**Monitor SQL queries:**

```python
# Trong sql_service.py
logger.info(f"Executing SQL: {query}")
```

## Troubleshooting

### Common Issues

**1. "GOOGLE_API_KEY not found"**
- Đảm bảo file `.env` tồn tại trong `backend/`
- Kiểm tra `GOOGLE_API_KEY` đã được set trong `.env`
- Restart Flask server sau khi update `.env`

**2. "Cannot connect to SQL Server"**
- Kiểm tra SQL Server đang chạy
- Verify connection string trong `.env`
- Đảm bảo ODBC Driver 17 đã được cài đặt
- Test connection với SQL Server Management Studio

**3. "Vector store not found"**
- Chạy ingestion script: `python scripts/ingest_data.py`
- Đảm bảo có documents trong `data/documents/`
- Kiểm tra `VECTOR_STORE_PATH` trong `.env`

**4. "Module not found" errors**
- Activate virtual environment: `venv\Scripts\activate`
- Reinstall dependencies: `pip install -r requirements.txt`

**5. Frontend không connect được backend**
- Kiểm tra backend đang chạy tại `http://localhost:5000`
- Verify CORS configuration trong `backend/app/__init__.py`
- Check browser console cho errors

**6. Streaming không hoạt động**
- Đảm bảo browser hỗ trợ EventSource API
- Check network tab trong browser DevTools
- Verify SSE response headers từ backend

### Performance Tips

- **Vector Search**: Giảm `k` value trong retriever nếu response chậm
- **SQL Queries**: Thêm indexes vào database tables
- **LLM Calls**: Giảm `LLM_TEMPERATURE` để response nhanh hơn
- **Chunk Size**: Tăng chunk size để giảm số embeddings cần tạo

## Production Deployment

### Security Checklist

- [ ] Đổi `SECRET_KEY` trong `.env`
- [ ] Set `FLASK_ENV=production`
- [ ] Không commit file `.env`
- [ ] Sử dụng HTTPS cho production
- [ ] Enable rate limiting cho API endpoints
- [ ] Restrict CORS origins
- [ ] Use strong SQL Server passwords
- [ ] Regularly update dependencies


## License

MIT
