# orflow - Subscription Engine

A robust, multi-tenant subscription billing engine built with FastAPI, SQLite (via SQLAlchemy/Alembic), Redis, and ARQ.

## Prerequisites

- **Python**: 3.13 or higher
- **Package Manager**: [uv](https://docs.astral.sh/uv/) (Fast Python package installer and resolver)
- **Redis**: Running locally or accessible via URL

## Local Setup Guide

Follow these steps to set up the project for local development.

### 1. Install Dependencies

The project uses `uv` for managing dependencies and the virtual environment. To install everything, simply run:

```bash
# This creates a .venv and installs all dependencies from pyproject.toml / uv.lock
uv sync
```

### 2. Environment Configuration

Create a `.env` file in the root of the project with the following basic configuration (adjust the values if necessary):

```env
DATABASE_URL=sqlite+aiosqlite:///./sub_eng.db
REDIS_URL=redis://localhost:6379
```

### 3. Database Migrations

Apply the Alembic migrations to set up your SQLite database schema:

```bash
uv run alembic upgrade head
```

### 4. Seed the Database

For local testing, you can seed the database with sample tenants and plans. This will create dummy data and print API keys for you to use when making requests:

```bash
uv run python scripts/seed.py
```
**Note:** Keep track of the generated `API Key`s printed in the console; you will need them to authenticate as a tenant via the `X-Tenant-Id` header (if your middleware expects it, or via standard auth depending on the endpoint).

### 5. Running the Development Server

Start the FastAPI application using Uvicorn with auto-reload enabled:

```bash
uv run uvicorn main:app --reload
```

The server will be available at `http://127.0.0.1:8000`.

### 6. API Documentation

Once the server is running, you can explore the API using the automatically generated documentation:
- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

## Project Structure Overview

- `app/`: Contains the core application logic, separated into domain modules:
  - `core/`: Global configurations, middleware, etc.
  - `db/`: Database connection and session management.
  - `tenants/`, `customers/`, `plans/`, `payment_methods/`: Core business domains with their own models, schemas, and routers.
- `alembic/`: SQLAlchemy database migration scripts.
- `scripts/`: Helper scripts (e.g., database seeding).
- `main.py`: The entry point for the FastAPI application.

## 7. Outbound Webhooks

The subscription engine sends real-time HTTP POST requests (webhooks) to tenant endpoints whenever significant state changes occur (e.g., `subscription.created`, `invoice.paid`).

### Delivery & Retries
Webhook delivery is handled asynchronously via Arq. If the tenant's endpoint fails to respond with a `2xx` success code or times out, the system automatically schedules a retry using an **exponential backoff strategy**. 

A maximum of 3 retries will be attempted with the following delays:
1. **1st Retry**: ~1 minute after the initial failure.
2. **2nd Retry**: ~5 minutes after the 1st retry.
3. **3rd Retry**: ~15 minutes after the 2nd retry.

If all 3 retries fail, the webhook event is marked as `failed` and no further attempts are made. Tenants can retrieve the delivery history of any event using the API.
