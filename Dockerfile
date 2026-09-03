FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY hwintern ./hwintern
COPY config.yaml companies.yaml ./
VOLUME ["/app/state"]
CMD ["python", "-m", "hwintern", "run", "--loop"]
