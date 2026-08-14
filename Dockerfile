FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CPU-only torch first so pip never pulls the CUDA wheel stack.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY pyproject.toml README.md ./
COPY api ./api
COPY pipeline ./pipeline
COPY web ./web
COPY db ./db

RUN pip install .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
