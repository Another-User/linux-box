# OptAware Agent — Multi-stage build
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY config/ config/
COPY models/ models/
COPY services/ services/
COPY perception/ perception/
COPY cognition/ cognition/
COPY planning/ planning/
COPY knowledge/ knowledge/
COPY portal/ portal/
COPY cli/ cli/
COPY *.py ./
COPY *.yaml ./

RUN pip install --no-cache-dir --prefix=/install .

# Runtime image
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    systemctl \
    iproute2 \
    curl \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -s /bin/false optaware

WORKDIR /app
COPY --from=builder /install /usr/local
COPY --from=builder /build /app

# Persistent data volumes
VOLUME ["/data/config", "/data/logs", "/data/knowledge", "/data/backups"]

# Config mount point
VOLUME ["/etc/optaware"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

ENTRYPOINT ["python", "daemon.py"]
CMD ["--config", "/etc/optaware/optaware.yaml", "--foreground"]
