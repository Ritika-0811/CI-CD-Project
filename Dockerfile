# ── Stage 1: build dependencies ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies into a separate prefix so we can copy just them
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app.py .

# Switch to non-root user
USER appuser

EXPOSE 5000

# Gunicorn as production WSGI server; workers tuned for I/O-bound Flask
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--timeout", "30", "app:app"]
