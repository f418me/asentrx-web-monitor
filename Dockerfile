# Stage 1: Builder Stage - Installiert die Abhängigkeiten
FROM python:3.12-slim AS builder

# Set environment variables for Poetry
ENV POETRY_VERSION=1.7.1
ENV POETRY_HOME="/opt/poetry"
ENV PATH="${POETRY_HOME}/bin:${PATH}"

ENV POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

WORKDIR /app

COPY poetry.lock pyproject.toml ./

RUN poetry install --no-dev --no-interaction --no-ansi --no-root


# Stage 2: Final Stage - Erstellt das schlanke Produktionsimage
FROM python:3.12-slim AS final

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

COPY --from=builder /usr/local/bin/ /usr/local/bin/


COPY . .

# Create a non-root user and switch to it
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --shell /bin/bash --create-home appuser

# Ensure the app directory and its contents are owned by the appuser
RUN chown -R appuser:appuser /app

USER appuser

# Command to run the application
CMD ["python", "main.py"]