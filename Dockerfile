FROM docker.io/library/python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps

FROM docker.io/redhat/ubi9:latest
RUN dnf install -y python3.12 python3.12-pip && dnf clean all
WORKDIR /app
COPY --from=builder /deps /app/deps
ENV PYTHONPATH=/app/deps
COPY main.py .
COPY run.sh /run.sh
RUN chmod +x /run.sh
EXPOSE 8080
ENTRYPOINT ["bash", "/run.sh"]
