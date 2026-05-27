#!/usr/bin/env bash
set -e

if [ -f /data/params ]; then
    set -a
    # shellcheck disable=SC1091
    source /data/params
    set +a
fi

: "${AMQP_HOST:?AMQP_HOST is required}"
: "${AMQP_USER:?AMQP_USER is required}"
: "${AMQP_PASS:?AMQP_PASS is required}"
: "${CART_HOST:?CART_HOST is required}"
: "${CART_PORT:?CART_PORT is required}"
: "${USER_HOST:?USER_HOST is required}"
: "${USER_PORT:?USER_PORT is required}"
: "${SHOP_PAYMENT_PORT:?SHOP_PAYMENT_PORT is required}"

export AMQP_HOST AMQP_USER AMQP_PASS
export CART_URL="http://${CART_HOST}:${CART_PORT}"
export USER_URL="http://${USER_HOST}:${USER_PORT}"
export PORT="${SHOP_PAYMENT_PORT}"

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
