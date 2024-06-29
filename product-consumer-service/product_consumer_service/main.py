import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Annotated, AsyncGenerator, List
from fastapi import Depends, FastAPI, HTTPException

from product_service.proto import product_pb2, operation_pb2

from product_service.db import create_tables, engine, get_session
from product_service.models import Product, ProductUpdate
from product_service.setting import BOOTSTRAP_SERVER, KAFKA_CONSUMER_GROUP_ID, KAFKA_PRODUCT_TOPIC
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from sqlmodel import Session, select


logging.basicConfig(level= logging.INFO)
logger = logging.getLogger(__name__)


MAX_RETRIES = 5
RETRY_INTERVAL = 10



@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info('Creating Tables')
    create_tables()
    logger.info("Tables Created")

    loop = asyncio.get_event_loop()
    task = loop.create_task(consume_products())
    
    yield

    task.cancel()
    await task


app = FastAPI(lifespan=lifespan, title="Product consumer Service", version='1.0.0')


# @app.get('/')
# async def root() -> Any:
#     return {"message": "Welcome to products section test"}




async def consume_products():
    retries = 0

    while retries < MAX_RETRIES:
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_PRODUCT_TOPIC,
                bootstrap_servers=BOOTSTRAP_SERVER,
                group_id=KAFKA_CONSUMER_GROUP_ID,
                auto_offset_reset='earliest',  # Start from the earliest message if no offset is committed
                enable_auto_commit=True,       # Enable automatic offset committing
                auto_commit_interval_ms=5000   # Interval for automatic offset commits
            )

            await consumer.start()
            logger.info("Consumer started successfully.")
            break
        except Exception as e:
            retries += 1
            logger.error(f"Error starting consumer, retry {retries}/{MAX_RETRIES}: {e}")
            if retries < MAX_RETRIES:
                await asyncio.sleep(RETRY_INTERVAL)
            else:
                logger.error("Max retries reached. Could not start consumer.")
                return

    try:
        async for msg in consumer:
            try:
                product = product_pb2.Product()
                product.ParseFromString(msg.value)
                logger.info(f"Received Message: {product}")

                with Session(engine) as session:
                    if product.operation == operation_pb2.OperationType.CREATE:
                        new_product = Product(
                            name=product.name,
                            product_id=product.product_id,
                            description=product.description,
                            price=product.price,
                            in_stock=product.in_stock,
                            category=product.category
                        )
                        session.add(new_product)
                        session.commit()
                        session.refresh(new_product)
                        logger.info(f'Product added to db: {new_product}')
                    
                    elif product.operation == operation_pb2.OperationType.UPDATE:
                        existing_product = session.exec(select(Product).where(Product.id == product.id)).first()
                        if existing_product:
                            existing_product.name = product.name
                            existing_product.product_id = product.product_id
                            existing_product.description = product.description
                            existing_product.price = product.price
                            existing_product.in_stock = product.in_stock
                            existing_product.category = product.category
                            session.add(existing_product)
                            session.commit()
                            session.refresh(existing_product)
                            logger.info(f'Product updated in db: {existing_product}')
                        else:
                            logger.warning(f"Product with ID {product.id} not found")

                    elif product.operation == operation_pb2.OperationType.DELETE:
                        existing_product = session.exec(select(Product).where(Product.id == product.id)).first()
                        if existing_product:
                            session.delete(existing_product)
                            session.commit()
                            logger.info(f"Product with ID {product.id} successfully deleted")
                        else:
                            logger.warning(f"Product with ID {product.id} not found for deletion")

            except Exception as e:
                logger.error(f"Error processing message: {e}")

    except asyncio.CancelledError:
        logger.info("Consumer task cancelled")
    finally:
        await consumer.stop()
        logger.info("Consumer stopped")
    return