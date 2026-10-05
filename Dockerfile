# Optional container image. The dashboard is prebuilt in frontend/dist.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# Package index for the build. pip reads these as environment variables; pass a
# mirror when files.pythonhosted.org is unreachable (e.g. from Iran without a VPN):
#   docker compose build --build-arg PIP_INDEX_URL=https://<mirror>/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY frontend/dist frontend/dist
COPY scripts scripts
COPY run.py mcp_server.py ./
ENV HOST=0.0.0.0 PORT=8765
EXPOSE 8765
CMD ["python", "run.py", "--no-browser"]
