import os
import logging
from datetime import datetime, timezone as dt_timezone, timedelta

logger = logging.getLogger(__name__)

# Constants for index rotation
MAX_INDEX_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10GB limit per index
MAX_RETAINED_INDICES = 5
INDEX_PREFIX = "logs-"
WRITE_ALIAS = "logs-write"
READ_ALIAS = "logs-all"

class LogStore:
    def __init__(self):
        self.es_url = os.environ.get('ELASTICSEARCH_URL', '')
        self.es_user = os.environ.get('ELASTICSEARCH_USER', '')
        self.es_password = os.environ.get('ELASTICSEARCH_PASSWORD', '')
        self.es_ca_cert = os.environ.get('ELASTICSEARCH_CA_CERT', '')
        self.es = None
        self.es_available = False
        self.mongo_db = None
        self.last_rotation_check_counter = 0
        self.rotation_check_frequency = 1000 # Check every 1000 ingested logs

    async def init(self, mongo_db):
        self.mongo_db = mongo_db
        if self.es_url:
            try:
                from elasticsearch import AsyncElasticsearch
                
                # Connection options
                es_options = {
                    "hosts": [self.es_url],
                }
                
                # Authentication if provided
                if self.es_user and self.es_password:
                    es_options["basic_auth"] = (self.es_user, self.es_password)
                
                # SSL/TLS configuration
                if self.es_ca_cert:
                    from pathlib import Path
                    cert_path = Path(self.es_ca_cert)
                    
                    # Robust resolution:
                    # 1. Check as provided (absolute or relative to CWD)
                    # 2. Check relative to this file
                    # 3. Check one level up (common in dev vs docker)
                    possible_paths = [
                        cert_path,
                        Path(__file__).parent / cert_path,
                        Path(__file__).parent.parent / cert_path
                    ]
                    
                    actual_cert = None
                    for p in possible_paths:
                        if p.exists() and p.is_file():
                            actual_cert = str(p.absolute())
                            break
                    
                    if actual_cert:
                        es_options["ca_certs"] = actual_cert
                        es_options["verify_certs"] = True
                        logger.info(f"Using Elasticsearch CA cert from {actual_cert}")
                    else:
                        logger.warning(f"Elasticsearch CA cert not found at {self.es_ca_cert} (checked multiple locations), continuing without verification.")
                        es_options["verify_certs"] = False
                elif self.es_url.startswith("https"):
                    # Default for https if no CA cert provided
                    es_options["verify_certs"] = False
                
                self.es = AsyncElasticsearch(**es_options)
                await self.es.info()
                self.es_available = True
                await self._setup_rolling_indices()
                logger.info("Elasticsearch connected successfully with rolling index support")
            except Exception as e:
                logger.warning(f"Elasticsearch not available, using MongoDB fallback: {e}")
                self.es_available = False
        else:
            logger.info("No ELASTICSEARCH_URL configured, using MongoDB for logs")

        try:
            await self.mongo_db.logs.create_index([("project_id", 1), ("timestamp", -1)])
            await self.mongo_db.logs.create_index([("level", 1)])
            await self.mongo_db.logs.create_index([("channel", 1)])
            await self.mongo_db.logs.create_index([("id", 1)], unique=True)
            await self.mongo_db.replays.create_index([("log_id", 1)], unique=True)
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def _setup_rolling_indices(self):
        """Ensures aliases and initial indices are properly configured."""
        try:
            # Check if our write alias exists
            write_alias_exists = await self.es.indices.exists_alias(name=WRITE_ALIAS)
            
            if not write_alias_exists:
                # Compatibility check: does the old single 'logs' index exist?
                old_index_exists = await self.es.indices.exists(index="logs")
                
                if old_index_exists:
                    # Migrate: Rename old index to the first rolling index
                    first_index_name = f"{INDEX_PREFIX}000001"
                    logger.info(f"Migrating legacy 'logs' index to {first_index_name}...")
                    await self.es.reindex(body={
                        "source": {"index": "logs"},
                        "dest": {"index": first_index_name}
                    }, wait_for_completion=True)
                    await self.es.indices.delete(index="logs")
                else:
                    # Fresh start
                    first_index_name = f"{INDEX_PREFIX}000001"
                    await self._create_physical_index(first_index_name)
                
                # Setup aliases
                await self.es.indices.update_aliases(body={
                    "actions": [
                        {"add": {"index": f"{INDEX_PREFIX}*", "alias": READ_ALIAS}},
                        {"add": {"index": first_index_name, "alias": WRITE_ALIAS}}
                    ]
                })
        except Exception as e:
            logger.error(f"Error during rolling index setup: {e}")
            self.es_available = False

    async def _create_physical_index(self, name):
        """Creates a new physical index with the standardized mapping."""
        await self.es.indices.create(index=name, body={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "id": {"type": "keyword"},
                    "level": {"type": "keyword"},
                    "message": {"type": "text", "analyzer": "standard"},
                    "channel": {"type": "keyword"},
                    "environment": {"type": "keyword"},
                    "project_id": {"type": "keyword"},
                    "project_name": {"type": "keyword"},
                    "timestamp": {"type": "date"},
                    "stack_trace": {"type": "text"},
                    "tags": {"type": "keyword"},
                    "grouped_hash": {"type": "keyword"},
                    "metadata": {"type": "object", "enabled": True},
                    "user_info": {"type": "object", "enabled": True},
                    "device_info": {"type": "object", "enabled": True}
                }
            }
        })
        logger.info(f"New physical index created: {name}")

    async def _check_and_rotate_if_needed(self):
        """Checks current write index size and rotates if it exceeds the limit."""
        try:
            # Get the actual index name behind the write alias
            res = await self.es.indices.get_alias(name=WRITE_ALIAS)
            current_write_index = list(res.keys())[0]
            
            # Check its size
            stats = await self.es.indices.stats(index=current_write_index, metric="store")
            current_size = stats['_all']['total']['store']['size_in_bytes']
            
            if current_size >= MAX_INDEX_SIZE_BYTES:
                logger.info(f"Index {current_write_index} size ({current_size} bytes) exceeds limit. Rotating...")
                await self._perform_rotation(current_write_index)
        except Exception as e:
            logger.error(f"Failed to check/rotate index: {e}")

    async def _perform_rotation(self, old_index):
        """Handles the creation of a new index and deletion of the oldest ones."""
        # 1. Generate next index name (e.g., logs-000002)
        try:
            current_num = int(old_index.split('-')[-1])
        except (ValueError, IndexError):
            current_num = 1
            
        new_index = f"{INDEX_PREFIX}{str(current_num + 1).zfill(6)}"
        
        # 2. Create the new index
        await self._create_physical_index(new_index)
        
        # 3. Swap aliases atomically
        await self.es.indices.update_aliases(body={
            "actions": [
                {"remove": {"index": old_index, "alias": WRITE_ALIAS}},
                {"add": {"index": new_index, "alias": WRITE_ALIAS}},
                {"add": {"index": new_index, "alias": READ_ALIAS}}
            ]
        })
        
        # 4. Enforce retention policy (keep only last 5 indices)
        indices_info = await self.es.indices.get(index=f"{INDEX_PREFIX}*")
        existing_indices = sorted(list(indices_info.keys()))
        
        if len(existing_indices) > MAX_RETAINED_INDICES:
            indices_to_delete = existing_indices[:-MAX_RETAINED_INDICES]
            for idx in indices_to_delete:
                await self.es.indices.delete(index=idx)
                logger.info(f"Retention policy: Deleted oldest index {idx}")

    async def index_log(self, log_doc):
        if self.es_available:
            try:
                # Write to the current active index via alias
                await self.es.index(index=WRITE_ALIAS, id=log_doc['id'], document=log_doc)
                
                # Check for rotation based on frequency
                self.last_rotation_check_counter += 1
                if self.last_rotation_check_counter >= self.rotation_check_frequency:
                    self.last_rotation_check_counter = 0
                    await self._check_and_rotate_if_needed()
            except Exception as e:
                logger.error(f"ES index error: {e}")
        
        mongo_doc = {**log_doc}
        await self.mongo_db.logs.insert_one(mongo_doc)

    def _build_es_must(self, filters, search_query):
        must = []
        if filters.get('project_ids'):
            must.append({"terms": {"project_id": filters['project_ids']}})
        elif filters.get('project_id'):
            must.append({"term": {"project_id": filters['project_id']}})
        if filters.get('level'):
            must.append({"term": {"level": filters['level']}})
        if filters.get('channel'):
            must.append({"term": {"channel": filters['channel']}})
        if filters.get('environment'):
            must.append({"term": {"environment": filters['environment']}})
        if filters.get('tags'):
            t = filters['tags']
            if isinstance(t, list):
                for tag in t:
                    must.append({"term": {"tags": tag}})
            else:
                must.append({"term": {"tags": t}})
        if filters.get('grouped_hash'):
            must.append({"term": {"grouped_hash": filters['grouped_hash']}})
        if filters.get('date_from') or filters.get('date_to'):
            range_q = {}
            if filters.get('date_from'):
                try:
                    df = filters['date_from']
                    if 'T' in df and len(df) == 16: df += ":00" # Add seconds if missing from datetime-local
                    dt_val = datetime.fromisoformat(df.replace('Z', '+00:00'))
                    if dt_val.tzinfo is None:
                        dt_val = dt_val.replace(tzinfo=dt_timezone.utc)
                    range_q['gte'] = dt_val
                except Exception as e:
                    print(f"ERROR: Failed to parse date_from '{filters.get('date_from')}': {e}")
                    range_q['gte'] = filters['date_from']
            if filters.get('date_to'):
                try:
                    dt = filters['date_to']
                    if 'T' in dt and len(dt) == 16: dt += ":59" # Add seconds if missing from datetime-local
                    dt_val = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                    if dt_val.tzinfo is None:
                        dt_val = dt_val.replace(tzinfo=dt_timezone.utc)
                    range_q['lte'] = dt_val
                except Exception as e:
                    print(f"ERROR: Failed to parse date_to '{filters.get('date_to')}': {e}")
                    range_q['lte'] = filters['date_to']
            must.append({"range": {"timestamp": range_q}})
        
        # Docker specific filters
        if filters.get('source'):
            must.append({"term": {"metadata.source": filters['source']}})
        if filters.get('container_name'):
            must.append({"term": {"metadata.container_name": filters['container_name']}})
            
        if search_query:
            must.append({"multi_match": {"query": search_query, "fields": ["message^2", "stack_trace", "tags"]}})
        return must

    def _build_mongo_query(self, filters, search_query):
        query = {}
        if filters.get('project_ids'):
            query['project_id'] = {"$in": filters['project_ids']}
        elif filters.get('project_id'):
            query['project_id'] = filters['project_id']
        if filters.get('level'):
            query['level'] = filters['level']
        if filters.get('channel'):
            query['channel'] = filters['channel']
        if filters.get('environment'):
            query['environment'] = filters['environment']
        if filters.get('tags'):
            t = filters['tags']
            if isinstance(t, list):
                query['tags'] = {"$all": t}
            else:
                query['tags'] = t
        if filters.get('grouped_hash'):
            query['grouped_hash'] = filters['grouped_hash']
        if filters.get('date_from') or filters.get('date_to'):
            ts_query = {}
            if filters.get('date_from'):
                try:
                    df = filters['date_from']
                    if 'T' in df and len(df) == 16: df += ":00"
                    # Ensure we handle various ISO formats properly
                    dt_val = datetime.fromisoformat(df.replace('Z', '+00:00'))
                    if dt_val.tzinfo is None:
                        dt_val = dt_val.replace(tzinfo=dt_timezone.utc)
                    ts_query['$gte'] = dt_val
                except Exception as e:
                    print(f"ERROR: Failed to parse date_from '{filters.get('date_from')}': {e}")
                    ts_query['$gte'] = filters['date_from']
            if filters.get('date_to'):
                try:
                    dt = filters['date_to']
                    if 'T' in dt and len(dt) == 16: dt += ":59"
                    dt_val = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                    if dt_val.tzinfo is None:
                        dt_val = dt_val.replace(tzinfo=dt_timezone.utc)
                    ts_query['$lte'] = dt_val
                except Exception as e:
                    print(f"ERROR: Failed to parse date_to '{filters.get('date_to')}': {e}")
                    ts_query['$lte'] = filters['date_to']
            if ts_query:
                query['timestamp'] = ts_query
        
        # Docker specific filters
        if filters.get('source'):
            query['metadata.source'] = filters['source']
        if filters.get('container_name'):
            query['metadata.container_name'] = filters['container_name']
            
        if search_query:
            query['$or'] = [
                {'message': {'$regex': search_query, '$options': 'i'}},
                {'stack_trace': {'$regex': search_query, '$options': 'i'}}
            ]
        return query

    async def search_logs(self, filters=None, page=1, size=50, search_query=None):
        if filters is None:
            filters = {}
        if self.es_available:
            return await self._es_search(filters, page, size, search_query)
        return await self._mongo_search(filters, page, size, search_query)

    async def _es_search(self, filters, page, size, search_query):
        must = self._build_es_must(filters, search_query)
        body = {
            "query": {"bool": {"must": must}} if must else {"match_all": {}},
            "sort": [{"timestamp": {"order": "desc"}}],
            "from": (page - 1) * size,
            "size": size
        }
        result = await self.es.search(index=READ_ALIAS, body=body)
        hits = result['hits']
        return {
            "logs": [hit['_source'] for hit in hits['hits']],
            "total": hits['total']['value'] if isinstance(hits['total'], dict) else hits['total'],
            "page": page,
            "size": size
        }

    async def _mongo_search(self, filters, page, size, search_query):
        query = self._build_mongo_query(filters, search_query)
        total = await self.mongo_db.logs.count_documents(query)
        cursor = self.mongo_db.logs.find(query, {"_id": 0})
        cursor = cursor.sort("timestamp", -1).skip((page - 1) * size).limit(size)
        logs = await cursor.to_list(size)
        return {"logs": logs, "total": total, "page": page, "size": size}

    async def search_groups(self, filters=None, page=1, size=20, search_query=None):
        if filters is None:
            filters = {}
        if self.es_available:
            return await self._es_search_groups(filters, page, size, search_query)
        return await self._mongo_search_groups(filters, page, size, search_query)

    async def _es_search_groups(self, filters, page, size, search_query):
        must = self._build_es_must(filters, search_query)
        
        # In ES, terms aggregation with top_hits is best for small sizes.
        # Deep pagination of groups would require composite aggregation or collapse.
        body = {
            "query": {"bool": {"must": must}} if must else {"match_all": {}},
            "size": 0,
            "aggs": {
                "groups": {
                    "terms": {
                        "field": "grouped_hash",
                        "size": page * size,
                        "order": {"latest_timestamp": "desc"}
                    },
                    "aggs": {
                        "latest_timestamp": {"max": {"field": "timestamp"}},
                        "earliest_timestamp": {"min": {"field": "timestamp"}},
                        "sample_log": {
                            "top_hits": {
                                "size": 1,
                                "sort": [{"timestamp": {"order": "desc"}}]
                            }
                        }
                    }
                },
                "total_count": {
                    "cardinality": {"field": "grouped_hash"}
                }
            }
        }
        
        result = await self.es.search(index=READ_ALIAS, body=body)
        buckets = result['aggregations']['groups']['buckets']
        
        start = (page - 1) * size
        end = start + size
        paged_buckets = buckets[start:end]
        
        groups = []
        for b in paged_buckets:
            log = b['sample_log']['hits']['hits'][0]['_source']
            log['count'] = b['doc_count']
            log['grouped_hash'] = b['key']['group']
            # Convert numeric timestamps from ES to ISO strings if needed
            log['first_seen'] = b['earliest_timestamp'].get('value_as_string') or str(b['earliest_timestamp'].get('value', ''))
            log['last_seen'] = b['latest_timestamp'].get('value_as_string') or str(b['latest_timestamp'].get('value', ''))
            groups.append(log)
            
        total = result['aggregations']['total_count']['value']
        
        return {"groups": groups, "total": total, "page": page, "size": size}

    async def _mongo_search_groups(self, filters, page, size, search_query):
        query = self._build_mongo_query(filters, search_query)
        
        # Add filter to only group logs that HAVE a grouped_hash
        # if filters don't already specify it
        if 'grouped_hash' not in query:
             query['grouped_hash'] = {"$ne": None}

        # Count pipeline
        count_pipeline = [
            {"$match": query},
            {"$group": {"_id": "$grouped_hash"}},
            {"$count": "total"}
        ]
        
        total_res = await self.mongo_db.logs.aggregate(count_pipeline).to_list(1)
        total = total_res[0]['total'] if total_res else 0
        
        # Grouping pipeline
        pipeline = [
            {"$match": query},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$grouped_hash",
                "latest_log": {"$first": "$$ROOT"},
                "count": {"$sum": 1},
                "first_seen": {"$min": "$timestamp"},
                "last_seen": {"$max": "$timestamp"}
            }},
            {"$sort": {"latest_log.timestamp": -1}},
            {"$skip": (page - 1) * size},
            {"$limit": size}
        ]
        
        cursor = self.mongo_db.logs.aggregate(pipeline)
        groups = []
        async for doc in cursor:
            log = doc['latest_log']
            log['count'] = doc['count']
            log['grouped_hash'] = doc['_id']
            log['first_seen'] = doc['first_seen']
            log['last_seen'] = doc['last_seen']
            if '_id' in log: del log['_id']
            groups.append(log)
            
        return {"groups": groups, "total": total, "page": page, "size": size}

    async def get_log(self, log_id):
        if self.es_available:
            try:
                result = await self.es.get(index=READ_ALIAS, id=log_id)
                return result['_source']
            except Exception:
                pass
        return await self.mongo_db.logs.find_one({"id": log_id}, {"_id": 0})

    async def delete_log(self, log_id):
        if self.es_available:
            try:
                search_res = await self.es.search(index=READ_ALIAS, body={
                    "query": {"term": {"id": log_id}},
                    "_source": False,
                    "size": 1
                })
                if search_res['hits']['hits']:
                    target_index = search_res['hits']['hits'][0]['_index']
                    await self.es.delete(index=target_index, id=log_id)
            except Exception:
                pass
        await self.mongo_db.logs.delete_one({"id": log_id})

    async def get_stats(self, project_id=None, user_id=None, allowed_project_ids=None, is_admin=False):
        if self.es_available:
            return await self._es_get_stats(project_id, user_id, allowed_project_ids, is_admin)
        return await self._mongo_get_stats(project_id, user_id, allowed_project_ids, is_admin)

    async def _mongo_get_stats(self, project_id, user_id, allowed_project_ids, is_admin):
        match_query = {}
        if project_id:
            match_query['project_id'] = project_id
        elif not is_admin:
            if allowed_project_ids is not None:
                if not allowed_project_ids:
                    return {"total": 0, "by_level": {}, "by_project": {}, "timeline": []}
                match_query['project_id'] = {"$in": allowed_project_ids}
            elif user_id:
                user_projects = await self.mongo_db.projects.find(
                    {"user_id": user_id}, {"_id": 0, "id": 1}
                ).to_list(100)
                project_ids = [p['id'] for p in user_projects]
                if project_ids:
                    match_query['project_id'] = {"$in": project_ids}
                else:
                    return {"total": 0, "by_level": {}, "by_project": {}, "timeline": []}

        # 1. Aggregation for Levels
        pipeline_level = [{"$match": match_query}, {"$group": {"_id": "$level", "count": {"$sum": 1}}}]
        by_level = {}
        async for doc in self.mongo_db.logs.aggregate(pipeline_level):
            by_level[doc['_id']] = doc['count']

        # 2. Aggregation for Projects
        pipeline_project = [{"$match": match_query}, {"$group": {"_id": "$project_name", "count": {"$sum": 1}}}]
        by_project = {}
        async for doc in self.mongo_db.logs.aggregate(pipeline_project):
            by_project[doc['_id']] = doc['count']

        # 3. Optimized Timeline Aggregation (No to_list(10000))
        now = datetime.now(dt_timezone.utc)
        day_ago = now - timedelta(hours=24)
        timeline_match = {**match_query, "timestamp": {"$gte": day_ago}}
        
        # We group by YYYY-MM-DDTHH using MongoDB aggregation
        pipeline_timeline = [
            {"$match": timeline_match},
            {"$group": {
                "_id": {
                    "$dateToString": { "format": "%Y-%m-%dT%H", "date": "$timestamp" }
                },
                "count": {"$sum": 1},
                "errors": {
                    "$sum": { "$cond": [{ "$in": ["$level", ["error", "critical"]] }, 1, 0] }
                }
            }},
            {"$sort": {"_id": 1}}
        ]
        
        timeline = []
        async for doc in self.mongo_db.logs.aggregate(pipeline_timeline):
            timeline.append({
                "hour": doc["_id"],
                "count": doc["count"],
                "errors": doc["errors"]
            })

        return {
            "total": sum(by_level.values()),
            "by_level": by_level,
            "by_project": by_project,
            "timeline": timeline
        }

    async def _es_get_stats(self, project_id, user_id, allowed_project_ids, is_admin):
        # Build filter same as _build_es_must
        filters = {}
        if project_id: filters['project_id'] = project_id
        elif not is_admin and allowed_project_ids is not None:
            filters['project_id'] = allowed_project_ids
            
        must = self._build_es_must(filters, None)
        query = {"bool": {"must": must}} if must else {"match_all": {}}
        
        now = datetime.now(dt_timezone.utc)
        day_ago = now - timedelta(hours=24)
        
        # Elasticsearch Aggregations
        body = {
            "query": query,
            "size": 0,
            "aggs": {
                "by_level": {
                    "terms": {"field": "level.keyword"}
                },
                "by_project": {
                    "terms": {"field": "project_name.keyword"}
                },
                "timeline": {
                    "filter": {
                        "range": {"timestamp": {"gte": day_ago.isoformat()}}
                    },
                    "aggs": {
                        "hourly": {
                            "date_histogram": {
                                "field": "timestamp",
                                "fixed_interval": "1h",
                                "format": "yyyy-MM-dd'T'HH"
                            },
                            "aggs": {
                                "errors": {
                                    "filter": {
                                        "terms": {"level.keyword": ["error", "critical"]}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        
        try:
            result = await self.es.search(index=READ_ALIAS, body=body)
            aggs = result['aggregations']
            
            by_level = {b['key']: b['doc_count'] for b in aggs['by_level']['buckets']}
            by_project = {b['key']: b['doc_count'] for b in aggs['by_project']['buckets']}
            
            timeline = []
            for b in aggs['timeline']['hourly']['buckets']:
                timeline.append({
                    "hour": b['key_as_string'],
                    "count": b['doc_count'],
                    "errors": b['errors']['doc_count']
                })
                
            return {
                "total": result['hits']['total']['value'] if isinstance(result['hits']['total'], dict) else result['hits']['total'],
                "by_level": by_level,
                "by_project": by_project,
                "timeline": timeline
            }
        except Exception as e:
            logger.error(f"Elasticsearch get_stats failed: {e}")
            # Fallback to MongoDB if ES fails during stats
            return await self._mongo_get_stats(project_id, user_id, allowed_project_ids, is_admin)

    async def close(self):
        if self.es and self.es_available:
            await self.es.close()

log_store = LogStore()
