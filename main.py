import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pika
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("payment")

AMQP_HOST = os.getenv("AMQP_HOST", "rabbitmq")
AMQP_USER = os.getenv("AMQP_USER", "guest")
AMQP_PASS = os.getenv("AMQP_PASS", "guest")
CART_URL = os.getenv("CART_URL", "http://cart:8003")
USER_URL = os.getenv("USER_URL", "http://user:8001")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))

EXCHANGE = "roboshop"
ROUTING_KEY = "orders"

rabbitmq_connection = None
rabbitmq_channel = None
http_client: httpx.AsyncClient | None = None


def connect_rabbitmq():
    global rabbitmq_connection, rabbitmq_channel
    credentials = pika.PlainCredentials(AMQP_USER, AMQP_PASS)
    for i in range(30):
        try:
            rabbitmq_connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=AMQP_HOST, credentials=credentials)
            )
            rabbitmq_channel = rabbitmq_connection.channel()
            rabbitmq_channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
            rabbitmq_channel.queue_declare(queue="orders", durable=True)
            rabbitmq_channel.queue_bind(queue="orders", exchange=EXCHANGE, routing_key=ROUTING_KEY)
            logger.info("Connected to RabbitMQ")
            return
        except Exception as e:
            logger.warning("RabbitMQ connection attempt %s/30 failed: %s", i + 1, e)
            time.sleep(2)
    raise Exception("Failed to connect to RabbitMQ")


def publish_order_event(order_event: dict) -> None:
    body = json.dumps(order_event)
    props = pika.BasicProperties(delivery_mode=2)
    try:
        rabbitmq_channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=body,
            properties=props,
        )
    except Exception as e:
        logger.error("Failed to publish order event: %s", e)
        connect_rabbitmq()
        rabbitmq_channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=ROUTING_KEY,
            body=body,
            properties=props,
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global http_client
    connect_rabbitmq()
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=10.0)
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    logger.info("Payment service ready (http_timeout=%ss, retries=%s)", HTTP_TIMEOUT, HTTP_RETRIES)
    yield
    await http_client.aclose()


app = FastAPI(title="RoboShop Payment Service", lifespan=lifespan)


class PaymentRequest(BaseModel):
    userId: str
    cityId: int


async def request_upstream(method: str, url: str, service_name: str) -> httpx.Response:
    last_err: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            return await http_client.request(method, url)
        except httpx.RequestError as e:
            last_err = e
            logger.warning(
                "%s %s attempt %s/%s failed: %s",
                method,
                url,
                attempt + 1,
                HTTP_RETRIES,
                e,
            )
            if attempt < HTTP_RETRIES - 1:
                await asyncio.sleep(0.15 * (attempt + 1))
    logger.error("%s service unavailable after %s attempts: %s", service_name, HTTP_RETRIES, last_err)
    raise HTTPException(status_code=503, detail=f"{service_name} service unavailable")


@app.get("/health")
def health():
    return {"status": "OK", "service": "payment"}


@app.post("/payment/process")
async def process_payment(request: PaymentRequest):
    logger.info(">> POST /payment/process userId=%s cityId=%s", request.userId, request.cityId)

    user_resp = await request_upstream("GET", f"{USER_URL}/validate/{request.userId}", "User")
    if user_resp.status_code != 200:
        logger.warning("Invalid user %s (status %s)", request.userId, user_resp.status_code)
        raise HTTPException(status_code=400, detail="Invalid user")
    user = user_resp.json()

    cart_resp = await request_upstream("GET", f"{CART_URL}/cart/{request.userId}", "Cart")
    if cart_resp.status_code != 200:
        logger.warning("Failed to get cart for %s (status %s)", request.userId, cart_resp.status_code)
        raise HTTPException(status_code=400, detail="Failed to get cart")
    cart = cart_resp.json()

    if not cart.get("items"):
        logger.warning("Cart empty for user %s", request.userId)
        raise HTTPException(status_code=400, detail="Cart is empty")

    total = sum(item["price"] * item["quantity"] for item in cart["items"])
    transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"

    order_event = {
        "userId": request.userId,
        "userEmail": user.get("email", ""),
        "userName": user.get("firstName", "Customer"),
        "items": cart["items"],
        "total": total,
        "cityId": request.cityId,
        "transactionId": transaction_id,
        "status": "PAID",
    }

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, publish_order_event, order_event)
    logger.info("Payment processed: %s for user %s", transaction_id, request.userId)

    try:
        await http_client.delete(f"{CART_URL}/cart/{request.userId}")
    except httpx.RequestError:
        logger.warning("Failed to clear cart after payment for user %s", request.userId)

    return {
        "status": "SUCCESS",
        "transactionId": transaction_id,
        "total": total,
    }
