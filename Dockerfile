FROM python:3.10

WORKDIR /app

# Dependencies first so code changes do not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x scripts/entrypoint.sh

CMD ["./scripts/entrypoint.sh"]
