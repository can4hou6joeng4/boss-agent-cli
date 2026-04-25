FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY mcp-server /app/mcp-server

RUN pip install --no-cache-dir ".[mcp]"

CMD ["boss-mcp"]
