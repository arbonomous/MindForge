FROM python:3.12-slim

WORKDIR /app

# Install minimal runtime dependencies first for better layer caching
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY configs/ ./configs/
COPY examples/ ./examples/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

# Default to a safe paper-trading simulation
CMD ["mindforge", "simulate", "--config", "configs/paper_ai_agents.yaml", "--steps", "300", "--no-live", "--report", "out/report.html"]
