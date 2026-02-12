FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY agent_sandbox/ agent_sandbox/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["python", "-m", "agent_sandbox", "start", "--host", "0.0.0.0", "--port", "8000"]
