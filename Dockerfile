# Optional container image. The dashboard is prebuilt in frontend/dist.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY frontend/dist frontend/dist
COPY run.py mcp_server.py ./
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "run.py"]
