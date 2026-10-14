# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for managing employees, recording attendance, handling attendance regularization, and generating attendance analytics.

## Tech Stack

- Python 3.11+
- FastAPI
- PyMongo
- MongoDB 6.0+

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/ADesai2708/HRone.git
cd HRone
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone_attendance
```

The application uses these variables to connect to MongoDB. Environment variables take precedence over values in `.env`.

Ensure MongoDB is running before starting the API.

### 5. Start the server

```bash
uvicorn app.main:app --port 8000
```

The API will be available at `http://localhost:8000`.

## API Documentation

- Health check: `GET /health`
- Interactive Swagger documentation: `/docs`
- OpenAPI documentation: `/openapi.json`

The health endpoint checks database connectivity.

## Testing

Run the test suite with:

```bash
python -m pytest tests/ -v
```

## Features

- Employee creation and paginated listing
- Attendance punch-in and punch-out
- Attendance filtering and manual regularization
- Monthly employee attendance analytics
- Department summaries and attendance trends
- Late-arrival leaderboard
- MongoDB query execution-plan inspection

## Design Documentation

- `REVIEW.md` — Code review findings and resolutions
- `DECISIONS.md` — Important implementation decisions and trade-offs