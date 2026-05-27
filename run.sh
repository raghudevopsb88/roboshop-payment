#!/usr/bin/env bash
set -e

if [ -f /data/params ]; then
    set -a
    # shellcheck disable=SC1091
    source /data/params
    set +a
fi

export AMQP_HOST="${AMQP_HOST:-rabbitmq}"
export AMQP_USER="${AMQP_USER:-guest}"
export AMQP_PASS="${AMQP_PASS:-guest}"
export CART_URL="${CART_URL:-http://${CART_HOST:-roboshop-cart}:${CART_PORT:-8080}}"
export USER_URL="${USER_URL:-http://${USER_HOST:-roboshop-user}:${USER_PORT:-8080}}"
export PORT="${SHOP_PAYMENT_PORT:-8080}"

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
