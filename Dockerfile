FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# State lives in /data so a mounted disk survives restarts; without a disk
# the directory still exists (state is just ephemeral).
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "python -m investment_bot serve --host 0.0.0.0 --port ${PORT:-8000} -c ${BOT_CONFIG:-config.deploy.yaml}"]
