FROM python:3.12-slim

# Unbuffered logs show up immediately in the host's log viewer; no .pyc files
# or pip cache bloating the image.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# State lives in /data so a mounted disk survives restarts; without a disk
# the directory still exists (state is just ephemeral).
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "python -m investment_bot serve --host 0.0.0.0 --port ${PORT:-8000} -c ${BOT_CONFIG:-config.deploy.yaml}"]
