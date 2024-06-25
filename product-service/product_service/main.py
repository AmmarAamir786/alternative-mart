import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Annotated, AsyncGenerator, List
from fastapi import Depends, FastAPI, HTTPException

from product_service import product_pb2

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


async def create_topic():
    admin_client = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP_SERVER)

    retries = 0

    while retries < MAX_RETRIES:
        try:
            await admin_client.start()
            topic_list = [NewTopic(name=KAFKA_PRODUCT_TOPIC,
                                num_partitions=2, 
                                replication_factor=1)]
            try:
                await admin_client.create_topics(new_topics=topic_list, validate_only=False)
                logger.info(f"Topic '{KAFKA_PRODUCT_TOPIC}' created successfully")
            except Exception as e:
                logger.info(f"Failed to create topic '{KAFKA_PRODUCT_TOPIC}': {e}")
            finally:
                await admin_client.close()
            return
        
        except KafkaConnectionError:
            retries += 1 
            logger.info(f"Kafka connection failed. Retrying {retries}/{MAX_RETRIES}...")
            await asyncio.sleep(RETRY_INTERVAL)
        
    raise Exception("Failed to connect to kafka broker after several retries")


async def kafka_producer():
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP_SERVER)
    await producer.start()
    try:
        yield producer
    finally:
        await producer.stop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:

    logger.info('Creating Topic')
    await create_topic()
    logger.info("Topic Created")

    logger.info('Creating Tables')
    create_tables()
    logger.info("Tables Created")

    loop = asyncio.get_event_loop()
    task = loop.create_task(consume_products())

    yield

    task.cancel()
    await task


app = FastAPI(lifespan=lifespan, title="Product Service", version='1.0.0')


# @app.get('/')
# async def root() -> Any:
#     return {"message": "Welcome to products section test"}



@app.post('/products/')
async def create_product(
    product: Product,
    producer: Annotated[AIOKafkaProducer, Depends(kafka_producer)]
):
    product_proto = product_pb2.Product(
        name=product.name,
        price=product.price,
        category=product.category,
        description=product.description,
        operation=product_pb2.OperationType.CREATE
    )

    logger.info(f"Received Message: {product_proto}")

    serialized_product = product_proto.SerializeToString()
    await producer.send_and_wait(KAFKA_PRODUCT_TOPIC, serialized_product)

    return {"Product": "Created"}


@app.put('/products/{product_id}')
async def edit_product(
    product_id: int,
    product_update: ProductUpdate,
    producer: Annotated[AIOKafkaProducer, Depends(kafka_producer)],
    session: Annotated[Session, Depends(get_session)]
):
    existing_product = session.exec(select(Product).where(Product.id == product_id)).first()
    if not existing_product:
        raise HTTPException(status_code=404, detail="Product not found")

    product_proto = product_pb2.Product(
        id=product_id,
        name=product_update.name,
        price=product_update.price,
        category=product_update.category,
        description=product_update.description,
        operation=product_pb2.OperationType.UPDATE
    )

    serialized_product = product_proto.SerializeToString()
    await producer.send_and_wait(KAFKA_PRODUCT_TOPIC, serialized_product)

    return {"Product": "Updated"}


@app.delete('/products/{product_id}')
async def delete_product(
    product_id: int,
    producer: Annotated[AIOKafkaProducer, Depends(kafka_producer)],
    session: Annotated[Session, Depends(get_session)]
):
    existing_product = session.exec(select(Product).where(Product.id == product_id)).first()
    if not existing_product:
        raise HTTPException(status_code=404, detail="Product not found")

    product_proto = product_pb2.Product(
        id=product_id,
        operation=product_pb2.OperationType.DELETE
    )

    serialized_product = product_proto.SerializeToString()
    await producer.send_and_wait(KAFKA_PRODUCT_TOPIC, serialized_product)

    return {"Product": "Deleted"}


@app.get("/products/", response_model=List[Product])
async def get_products(session: Annotated[Session, Depends(get_session)]):
    products = session.exec(select(Product)).all()
    return products


@app.get("/products/{product_id}", response_model=Product)
async def get_product(product_id: int, session: Annotated[Session, Depends(get_session)]):
    product = session.exec(select(Product).where(Product.id == product_id)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product



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
                    if product.operation == product_pb2.OperationType.CREATE:
                        new_product = Product(
                            name=product.name,
                            description=product.description,
                            price=product.price,
                            category=product.category
                        )
                        session.add(new_product)
                        session.commit()
                        session.refresh(new_product)
                        logger.info(f'Product added to db: {new_product}')
                    
                    elif product.operation == product_pb2.OperationType.UPDATE:
                        existing_product = session.exec(select(Product).where(Product.id == product.id)).first()
                        if existing_product:
                            existing_product.name = product.name
                            existing_product.description = product.description
                            existing_product.price = product.price
                            existing_product.category = product.category
                            session.add(existing_product)
                            session.commit()
                            session.refresh(existing_product)
                            logger.info(f'Product updated in db: {existing_product}')
                        else:
                            logger.warning(f"Product with ID {product.id} not found")

                    elif product.operation == product_pb2.OperationType.DELETE:
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