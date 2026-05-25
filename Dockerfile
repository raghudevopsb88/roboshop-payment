FROM docker.io/redhat/ubi9:latest AS builder

RUN dnf install -y python3 python3-pip gcc python3-devel && dnf clean all

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt

FROM docker.io/redhat/ubi9:latest

RUN dnf install -y python3 && dnf clean all

ENV INSTANA_SERVICE_NAME=payment \
    PATH="/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /venv /venv
COPY *.py ./
COPY payment.ini ./

EXPOSE 8080
CMD ["uwsgi", "--ini", "payment.ini"]
