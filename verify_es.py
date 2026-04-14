import asyncio
import os
import logging
from dotenv import load_dotenv
from pathlib import Path
from elasticsearch import AsyncElasticsearch

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_connection():
    # Load environment variables
    root_dir = Path(__file__).parent
    load_dotenv(root_dir / '.env')

    es_url = os.environ.get('ELASTICSEARCH_URL')
    es_user = os.environ.get('ELASTICSEARCH_USER')
    es_password = os.environ.get('ELASTICSEARCH_PASSWORD')
    es_ca_cert = os.environ.get('ELASTICSEARCH_CA_CERT')

    logger.info(f"Connecting to {es_url}...")
    
    es_options = {
        "hosts": [es_url],
    }

    if es_user and es_password:
        es_options["basic_auth"] = (es_user, es_password)
        logger.info(f"Using basic auth: {es_user}")

    if es_ca_cert:
        if os.path.exists(es_ca_cert):
            es_options["ca_certs"] = es_ca_cert
            es_options["verify_certs"] = True
            logger.info(f"Using CA cert: {es_ca_cert}")
        else:
            logger.warning(f"CA cert not found at {es_ca_cert}")
            es_options["verify_certs"] = False
    elif es_url.startswith("https"):
        es_options["verify_certs"] = False
        logger.info("HTTPS without CA cert, skipping verification")

    es = AsyncElasticsearch(**es_options)
    
    try:
        info = await es.info()
        logger.info("Successfully connected to Elasticsearch!")
        logger.info(f"Cluster info: {info}")
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
    finally:
        await es.close()

if __name__ == "__main__":
    asyncio.run(test_connection())
