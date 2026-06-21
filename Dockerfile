FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY victoria/ victoria/

RUN mkdir -p data models

CMD ["uvicorn", "victoria.main:app", "--host", "0.0.0.0", "--port", "8000"]
