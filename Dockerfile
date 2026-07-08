FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV KEIRIN_HOST=0.0.0.0
EXPOSE 8765

CMD ["python", "server.py"]
