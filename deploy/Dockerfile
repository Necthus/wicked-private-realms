FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
EXPOSE 8443/tcp
CMD ["python", "run_private_server.py", "--config", "server-config.json"]
