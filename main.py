import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import aio_pika
import httpx
from aio_pika import DeliveryMode, ExchangeType
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

rabbit_connection: aio_pika.RobustConnection | None = None
rabbit_exchange: aio_pika.Exchange | None = None
http_client: httpx.AsyncClient | None = None


async def setup_rabbitmq() -> None:
    global rabbit_connection, rabbit_exchange
    for attempt in range(30):
        try:
            rabbit_connection = await aio_pika.connect_robust(
                host=AMQP_HOST,
                port=5672,
                login=AMQP_USER,
                password=AMQP_PASS,
            )
            channel = await rabbit_connection.channel()
            rabbit_exchange = await channel.declare_exchange(
                EXCHANGE, ExchangeType.DIRECT, durable=True
            )
            queue = await channel.declare_queue("orders", durable=True)
            await queue.bind(rabbit_exchange, routing_key=ROUTING_KEY)
            logger.info("Connected to RabbitMQ at %s", AMQP_HOST)
            return
        except Exception as exc:
            logger.warning(
                "RabbitMQ connection attempt %s/30 failed: %s", attempt + 1, exc
            )
            rabbit_connection = None
            rabbit_exchange = None
            await asyncio.sleep(2)
    raise RuntimeError("Failed to connect to RabbitMQ")


async def publish_order_event(order_event: dict) -> None:
    if rabbit_exchange is None:
        raise RuntimeError("RabbitMQ exchange not initialized")

    body = json.dumps(order_event).encode()
    await rabbit_exchange.publish(
        aio_pika.Message(body=body, delivery_mode=DeliveryMode.PERSISTENT),
        routing_key=ROUTING_KEY,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global http_client
    await setup_rabbitmq()
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=10.0)
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    logger.info("Payment service ready (http_timeout=%ss, retries=%s)", HTTP_TIMEOUT, HTTP_RETRIES)
    yield
    await http_client.aclose()
    if rabbit_connection is not None:
        await rabbit_connection.close()


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

    try:
        await publish_order_event(order_event)
    except Exception as exc:
        logger.error("Failed to publish order event: %s", exc)
        raise HTTPException(status_code=503, detail="Message broker unavailable") from exc

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
