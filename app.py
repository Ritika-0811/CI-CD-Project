import os
import logging
import redis
from flask import Flask, jsonify

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

redis_client = None

def connect_redis():
    global redis_client
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=3)
        client.ping()
        redis_client = client
        logger.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except Exception as e:
        redis_client = None
        logger.error(f"Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT} — {e}")

# Attempt Redis connection on startup
connect_redis()


@app.route("/")
def index():
    return jsonify({"status": "Application Running"}), 200


@app.route("/health")
def health():
    return jsonify({"status": "OK"}), 200


@app.route("/ready")
def ready():
    """Readiness probe — only healthy if Redis is reachable."""
    try:
        if redis_client is None:
            raise RuntimeError("Redis client not initialised")
        redis_client.ping()
        return jsonify({"status": "ready", "redis": "connected"}), 200
    except Exception as e:
        logger.warning(f"Readiness check failed: {e}")
        return jsonify({"status": "not ready", "redis": "unreachable"}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
