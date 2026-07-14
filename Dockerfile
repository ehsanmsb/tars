# Minimal image for TARS.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application source (flat layout, no package directory).
COPY app.py config.py logger.py kubernetes_client.py tar_checker.py ./

# Run as a dedicated non-root user.
RUN groupadd --system --gid 10001 tars && \
    useradd --system --uid 10001 --gid 10001 --no-create-home tars
USER 10001:10001

ENTRYPOINT ["python", "-u", "app.py"]
