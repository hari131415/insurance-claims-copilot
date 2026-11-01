FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /app/data /app/uploads \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "claims_copilot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
