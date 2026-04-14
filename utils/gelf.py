import json
import logging
import asyncio

logger = logging.getLogger(__name__)

# Syslog levels to LogForge levels (kept for backward compat with shippers like Docker/Fluentd)
# 0: Emergency, 1: Alert, 2: Critical, 3: Error
# 4: Warning, 5: Notice, 6: Informational, 7: Debug
ALLOWED_LEVELS = {'debug', 'info', 'warning', 'error', 'critical'}

SYSLOG_TO_LEVEL = {
    0: 'critical', # Emergency
    1: 'critical', # Alert
    2: 'critical', # Critical
    3: 'error',    # Error
    4: 'warning',  # Warning
    5: 'info',     # Notice
    6: 'info',     # Informational
    7: 'debug'     # Debug
}

LEVEL_TO_SYSLOG = {
    'critical': 2,
    'error': 3,
    'warning': 4,
    'info': 6,
    'debug': 7
}

def parse_gelf_payload(payload_str: str) -> dict:
    """
    Parses a string (JSON) GELF payload and converts it to a dict that matches
    LogForge's internal LogIngest model format.
    """
    try:
        data = json.loads(payload_str)
    except Exception as e:
        logger.error(f"Failed to parse GELF JSON: {e}")
        return None

    if 'short_message' not in data:
        logger.error("GELF payload missing 'short_message'")
        return None

    # Determine level — accepts string ('error', 'debug'...) or syslog integer (0-7)
    gelf_level_raw = data.get('level', 'info')
    
    if isinstance(gelf_level_raw, int):
        # Syslog integer from Docker/Fluentd/Graylog shippers
        level_str = SYSLOG_TO_LEVEL.get(gelf_level_raw)
        if level_str is None:
            logger.error(f"Invalid GELF syslog level integer: {gelf_level_raw}. Allowed: 0-7")
            return {"error": f"Invalid syslog level integer '{gelf_level_raw}'. Allowed: 0-7."}
        syslog_level = gelf_level_raw # Keep the original integer as syslog_level
    elif isinstance(gelf_level_raw, str):
        level_str = gelf_level_raw.lower()
        if level_str not in ALLOWED_LEVELS:
            logger.error(f"Invalid GELF level string: '{gelf_level_raw}'")
            return {"error": f"Invalid log level '{gelf_level_raw}'. Allowed values are: {', '.join(sorted(ALLOWED_LEVELS))}."}
        syslog_level = LEVEL_TO_SYSLOG.get(level_str, 6)
    else:
        logger.error(f"GELF level must be a string or integer, got: {type(gelf_level_raw)}")
        return {"error": "GELF 'level' must be a string (e.g. 'error') or a syslog integer (0-7)."}

    # Extract metadata and built-ins
    metadata = {}
    api_key = None
    stack_trace = None
    environment = 'production'
    channel = 'default'

    for k, v in data.items():
        if k.startswith('_'): # Custom field in GELF
            if k == '_api_key':
                api_key = str(v)
            elif k == '_environment':
                environment = str(v)
            elif k == '_channel':
                channel = str(v)
            elif k == '_stack_trace':
                stack_trace = str(v)
            else:
                metadata[k[1:]] = v  # strip the underscore for metadata keys

    # Use full_message as stack trace if stack_trace is empty and full_message exists
    if not stack_trace and data.get('full_message'):
        stack_trace = data['full_message']

    host = data.get('host', 'unknown')

    return {
        "level": level_str,
        "message": data['short_message'],
        "channel": channel,
        "environment": environment,
        "metadata": metadata,
        "stack_trace": stack_trace,
        "device_info": {"ip": host},
        "tags": [],
        "api_key": api_key,  # Useful for UDP where header isn't available
        "gelf_version": data.get('version', '1.1'),
        "source_host": host,
        "syslog_level": syslog_level
    }

class GelfUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, ingestion_queue, db, ws_manager, log_store, background_tasks_factory):
        self.ingestion_queue = ingestion_queue
        self.db = db
        self.ws_manager = ws_manager
        self.log_store = log_store
        self.background_tasks_factory = background_tasks_factory
        super().__init__()

    def connection_made(self, transport):
        self.transport = transport
        peername = transport.get_extra_info('peername')
        logger.info(f"GELF UDP server listening")

    def datagram_received(self, data, addr):
        # We need to process this in the background to not block the UDP reciever
        asyncio.create_task(self.process_datagram(data, addr))

    async def process_datagram(self, data, addr):
        try:
            # GELF UDP payloads can be gzip or zlib compressed, but for MVP we
            # handle flat JSON. If it starts with b'{', it's uncompressed JSON.
            # GELF chunking (magic bytes 0x1e 0x0f) is skipped for simplicity.
            # If compressed, it starts with 0x1f 0x8b (gzip) or 0x78 (zlib).
            
            payload = data
            if data.startswith(b'\x1f\x8b'):
                import gzip
                payload = gzip.decompress(data)
            elif data.startswith(b'\x78'):
                import zlib
                payload = zlib.decompress(data)

            payload_str = payload.decode('utf-8')
            parsed_log = parse_gelf_payload(payload_str)
            
            if not parsed_log:
                return

            api_key = parsed_log.pop("api_key", None)
            if not api_key:
                logger.warning(f"GELF UDP log rejected: missing _api_key from {addr}")
                return

            # Verify project by API key
            project = await self.db.projects.find_one({"api_key": api_key})
            if not project:
                logger.warning(f"GELF UDP log rejected: invalid API key from {addr}")
                return

            # Prepare document
            import uuid
            from datetime import datetime, timezone
            from routes.log_routes import generate_log_hash, _check_alerts

            log_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            grouped_hash = generate_log_hash(parsed_log['level'], parsed_log['message'], parsed_log['stack_trace'])

            device_info = parsed_log.get('device_info', {})
            if device_info.get("ip") == "unknown":
                device_info['ip'] = addr[0]

            log_doc = {
                "id": log_id,
                "level": parsed_log['level'],
                "message": parsed_log['message'],
                "channel": parsed_log['channel'],
                "environment": parsed_log['environment'] or project.get('environment', 'production'),
                "project_id": project['id'],
                "project_name": project['name'],
                "metadata": parsed_log['metadata'],
                "stack_trace": parsed_log['stack_trace'],
                "user_info": None,
                "device_info": device_info,
                "tags": parsed_log['tags'],
                "timestamp": now,
                "grouped_hash": grouped_hash,
                "ingest_protocol": "gelf-udp",
                "gelf_version": parsed_log.get("gelf_version"),
                "source_host": parsed_log.get("source_host"),
                "syslog_level": parsed_log.get("syslog_level")
            }

            # Fire alerts asynchronously
            email_sent = await _check_alerts(log_doc, None) # Background Tasks factory not easily available here, we'll use None and standard async Create_task inside _check_alerts
            log_doc["alert_email_sent"] = email_sent
            
            # Queue to log worker
            await self.ingestion_queue.put(log_doc)
            
        except Exception as e:
            logger.error(f"Error processing GELF Datagram from {addr}: {e}")
