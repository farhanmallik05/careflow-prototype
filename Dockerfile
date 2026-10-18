FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN python -m careflow.seed --db careflow.db
CMD ["python", "-m", "uvicorn", "careflow.api:app", "--host", "0.0.0.0", "--port", "8000"]
