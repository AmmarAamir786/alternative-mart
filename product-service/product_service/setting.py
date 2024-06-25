from pydantic import Secret
from starlette.config import Config

try:
    config = Config(".env")

except FileNotFoundError:
    config = Config()



DATABASE_URL = config("DATABASE_URL", cast=str)
TEST_DATABASE_URL = config("TEST_DATABASE_URL", cast=str)


BOOTSTRAP_SERVER = config("BOOTSTRAP_SERVER", cast=str)
KAFKA_PRODUCT_TOPIC = config("KAFKA_PRODUCT_TOPIC", cast=str)
KAFKA_CONSUMER_GROUP_ID = config("KAFKA_CONSUMER_GROUP_ID", cast=str)
