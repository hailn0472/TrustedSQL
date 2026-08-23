# Frontend - Chatbot RAG + Text-to-SQL

This directory contains the frontend application for the chatbot. It's a single-page application built with React and Vite, providing a clean and responsive user interface for interacting with the backend chatbot API.

## ⚙️ Tech Stack

- **UI Framework**: React 18+
- **Build Tool**: Vite
- **Styling**: CSS Modules / Plain CSS
- **API Communication**: Fetch API with Server-Sent Events (SSE) for streaming
- **Dependency Management**: npm

## 📋 Prerequisites

- **Node.js**: Version 18 or higher.
- **npm**: Usually comes bundled with Node.js.
- **Running Backend**: The backend server must be running to handle API requests. See the [backend README](../backend/README.md) for setup instructions.

## 🚀 Setup and Installation

1.  **Navigate to the frontend directory:**

    ```bash
    cd frontend
    ```

2.  **Install dependencies:**

    This command will download and install all the necessary packages defined in `package.json`.

    ```bash
    npm install
    ```

## ▶️ Running the Frontend

To start the local development server, run the following command:

```bash
npm run dev
```

Vite will start the server, typically on `http://localhost:3000`. The exact address will be shown in your terminal.

Open your web browser and navigate to this address to see the application and start chatting.

### Connecting to the Backend

The frontend is configured to connect to the backend API at `http://localhost:5000/api`. This is handled by a proxy setting in the `vite.config.js` file.

If your backend is running on a different address or port, you will need to update the `proxy` target in `frontend/vite.config.js`:

```javascript
// vite.config.js
export default defineConfig({
  // ... other settings
  server: {
    proxy: {
      '/api': {
        target: 'http://your-backend-address:port', // Change this line
        changeOrigin: true,
      },
    },
  },
});
```

## 📁 Project Structure

```
frontend/
├── public/              # Static assets
├── src/
│   ├── components/      # Reusable React components (e.g., ChatWindow, Message)
│   ├── hooks/           # Custom hooks (e.g., useChat for managing chat logic)
│   ├── App.css          # Main application styles
│   ├── App.jsx          # The root React component
│   ├── index.css        # Global styles
│   └── main.jsx         # The entry point of the React application
├── .gitignore           # Files to be ignored by Git
├── index.html           # The main HTML file
├── package.json         # Project dependencies and scripts
├── package-lock.json    # Locked dependency versions
└── vite.config.js       # Vite build and server configuration
```

## 📦 Build for Production

To create an optimized production build of the application, run:

```bash
npm run build
```

The output will be placed in the `frontend/dist` directory. You can then serve these static files with any web server.
