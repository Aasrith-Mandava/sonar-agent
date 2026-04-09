#!/bin/bash
set -e

echo "Starting SonarQube Auto-Fix Agent..."

# Start Backend
echo "Running migrations..."
cd backend
source venv/bin/activate
alembic upgrade head

echo "Starting Backend API..."
uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# Start Frontend
echo "Starting Frontend..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!

function cleanup {
  echo "Shutting down servers..."
  kill $BACKEND_PID
  kill $FRONTEND_PID
  exit
}

trap cleanup SIGINT SIGTERM

echo "Servers running. Press Ctrl+C to stop."
wait $BACKEND_PID $FRONTEND_PID
