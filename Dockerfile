FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 10001 receptionist \
    && adduser --system --uid 10001 --ingroup receptionist receptionist

COPY requirements.lock pyproject.toml README.md LICENSE ./
COPY receptionist ./receptionist
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps . \
    && python -m receptionist.agent download-files

COPY config ./config

RUN mkdir -p /app/messages /app/transcripts /app/recordings \
    && chown -R receptionist:receptionist /app

USER receptionist

EXPOSE 8081

CMD ["python", "-m", "receptionist.agent", "start", "--log-level", "info"]
