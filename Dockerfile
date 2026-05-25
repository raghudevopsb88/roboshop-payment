FROM docker.io/library/python:3.11 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM docker.io/redhat/ubi9:latest
RUN dnf install -y python3 python3-pip && dnf clean all
ENV INSTANA_SERVICE_NAME=payment
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY payment.ini ./
EXPOSE 8080
CMD ["uwsgi", "--ini", "payment.ini"]
