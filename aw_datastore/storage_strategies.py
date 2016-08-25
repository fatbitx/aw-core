import json
import iso8601
import os
import logging
import sys
import copy
from typing import List, Union, Sequence
from datetime import datetime

import appdirs
from shutil import rmtree

from aw_core.models import Event

try:
    import pymongo
except ImportError:  # pragma: no cover
    logging.warning("Could not import pymongo, not available as a datastore backend")

try:
    from tinydb import TinyDB, Query, where
    from tinydb.storages import JSONStorage
    from tinydb_serialization import Serializer, SerializationMiddleware
except ImportError: # pragma: no cover
    logging.warning("Could not import tinydb, not available as a datastore backend")

logger = logging.getLogger("aw.datastore.strategies")


class StorageStrategy():
    """
    Interface for storage methods.
    """

    def __init__(self, testing):
        raise NotImplementedError

    def create_bucket(self, bucket_id, type_id, client, hostname, created, name=None):
        raise NotImplementedError

    def delete_bucket(self, bucket_id):
        raise NotImplementedError

    def get_metadata(self, bucket: str):
        raise NotImplementedError

    def get_events(self, bucket: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        raise NotImplementedError

    def buckets(self):
        raise NotImplementedError

    def insert_one(self, bucket: str, event: Event):
        raise NotImplementedError

    def insert_many(self, bucket: str, events: List[Event]):
        for event in events:
            self.insert_one(bucket, event)

    def replace_last(self, bucket_id, event):
        raise NotImplementedError


class TinyDbStorage():
    """
    TinyDB storage method
    """

    class DateTimeSerializer(Serializer):
        OBJ_CLASS = datetime  # The class this serializer handles

        def encode(self, obj):
            return obj.isoformat()

        def decode(self, s):
            return iso8601.parse_date(s)
    
    def __init__(self, testing):
        # Create dirs
        self.user_data_dir = appdirs.user_data_dir("aw-server", "activitywatch")
        self.buckets_dir = os.path.join(self.user_data_dir, "testing" if testing else "", "buckets")
        if not os.path.exists(self.buckets_dir):
            os.makedirs(self.buckets_dir)

        self.serializer = SerializationMiddleware(JSONStorage)
        self.serializer.register_serializer(self.DateTimeSerializer(), 'DateTime')
        self.tinydb_kwargs = {"storage": self.serializer}

        self.db = {}
        for bucket_id in os.listdir(self.buckets_dir):
            self._add_bucket(bucket_id)
   
    def _add_bucket(self, bucket_id: str):
        dbfile = "{}/{}.json".format(self._get_bucket_dir(bucket_id), bucket_id)
        self.db[bucket_id] = TinyDB(dbfile, **self.tinydb_kwargs)

    def _get_bucket_dir(self, bucket_id):
        return os.path.join(self.buckets_dir, bucket_id)

    def get_events(self, bucket_id: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        if limit <= 0:
            limit = sys.maxsize
        # Get all events
        events = []
        for e in self.db[bucket_id].all()[::-1]:
            events.append(Event(**e))
        #events = [Event(**e) for e in self.db[bucket_id].all()][::-1]
        # Sort by timestamp
        sorted(events, key=lambda k: k['timestamp'])
        for event in events:
            print(type(event['timestamp'][0]))
            if not isinstance(event['timestamp'][0], datetime):
                raise BaseException(event['timestamp'])
        # Filter endtime
        if endtime:
            e = []
            for event in events:
                if event['timestamp'] < endtime:
                    e.append(event)
            events = e
        # Limit
        events = events[:limit]
        # Filter starttime
        if starttime:
            e = []
            for event in events:
                if event['timestamp'] > starttime:
                    e.append(event)
            events = e
        # Return
        return events

    def buckets(self):
        buckets = {}
        for bucket in self.db:
            buckets[bucket] = self.get_metadata(bucket)
        return buckets

    def get_metadata(self, bucket_id: str):
        metafile = os.path.join(self._get_bucket_dir(bucket_id), "metadata.json")
        with open(metafile, 'r') as f:
            metadata = json.load(f)
        return metadata
    
    def insert_one(self, bucket_id: str, event: Event):
        self.db[bucket_id].insert(copy.deepcopy(event))

    def insert_many(self, bucket_id: str, events: List[Event]):
        self.db[bucket_id].insert_multiple(copy.deepcopy(events))

    def replace_last(self, bucket_id, event):
        e = self.db[bucket_id].get(where('timestamp') == self.get_events(bucket_id, 1)[0]["timestamp"])
        self.db[bucket_id].remove(eids=[e.eid])
        self.insert_one(bucket_id, event)

    def create_bucket(self, bucket_id, type_id, client, hostname, created, name=None):
        bucket_dir = self._get_bucket_dir(bucket_id)
        if not os.path.exists(bucket_dir):
            os.makedirs(bucket_dir)
        if not name:
            name = bucket_id
        metadata = {
            "id": bucket_id,
            "name": name,
            "type": type_id,
            "client": client,
            "hostname": hostname,
            "created": created
        }
        with open(os.path.join(bucket_dir, "metadata.json"), "w") as f:
            f.write(json.dumps(metadata))
        self._add_bucket(bucket_id)

    def delete_bucket(self, bucket_id):
        self.db.pop(bucket_id)
        rmtree(self._get_bucket_dir(bucket_id))

    

class MongoDBStorageStrategy(StorageStrategy):
    """Uses a MongoDB server as backend"""

    def __init__(self, testing):
        self.logger = logger.getChild("mongodb")

        self.client = pymongo.MongoClient(serverSelectionTimeoutMS=5000)
        # Try to connect to the server to make sure that it's available
        # If it isn't, it will raise pymongo.errors.ServerSelectionTimeoutError
        self.client.server_info()

        self.db = self.client["activitywatch" + ("-testing" if testing else "")]

    def create_bucket(self, bucket_id, type_id, client, hostname, created, name=None):
        if not name:
            name = bucket_id
        metadata = {
            "_id": "metadata",
            "id": bucket_id,
            "name": name,
            "type": type_id,
            "client": client,
            "hostname": hostname,
            "created": created,
        }
        self.db[bucket_id]["metadata"].insert_one(metadata)

    def delete_bucket(self, bucket_id):
        self.db[bucket_id]["events"].drop()
        self.db[bucket_id]["metadata"].drop()

    def buckets(self):
        bucketnames = set()
        for bucket_coll in self.db.collection_names():
            bucketnames.add(bucket_coll.split('.')[0])
        buckets = {}
        for bucket_id in bucketnames:
            buckets[bucket_id] = self.get_metadata(bucket_id)
        return buckets

    def get_metadata(self, bucket_id: str):
        metadata = self.db[bucket_id]["metadata"].find_one({"_id": "metadata"})
        if metadata:
            del metadata["_id"]
        return metadata

    def get_events(self, bucket_id: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        query_filter = {}
        if starttime:
            query_filter["timestamp"] = {}
            query_filter["timestamp"]["$gt"] = starttime
        if endtime:
            if "timestamp" not in query_filter:
                query_filter["timestamp"] = {}
            query_filter["timestamp"]["$lt"] = endtime
        if limit <= 0:
            limit = 10**9
        print(query_filter)
        return list(self.db[bucket_id]["events"].find(query_filter).sort([("timestamp", -1)]).limit(limit))

    def insert_one(self, bucket: str, event: Event):
        # .copy is needed because otherwise mongodb inserts a _id field into the event
        self.db[bucket]["events"].insert_one(event.copy())

    def replace_last(self, bucket_id, event):
        last_event = list(self.db[bucket_id]["events"].find().sort([("timestamp", -1)]).limit(1))[0]
        print(last_event)
        self.db[bucket_id]["events"].replace_one({"_id": last_event["_id"]}, event.to_json_dict())


class MemoryStorageStrategy(StorageStrategy):
    """For storage of data in-memory, useful primarily in testing"""

    def __init__(self, testing):
        self.logger = logger.getChild("memory")
        # self.logger.warning("Using in-memory storage, any events stored will not be persistent and will be lost when server is shut down. Use the --storage parameter to set a different storage method.")
        self.db = {}  # type: Mapping[str, Mapping[str, List[Event]]]
        self._metadata = {}

    def create_bucket(self, bucket_id, type_id, client, hostname, created, name=None):
        if not name:
            name = bucket_id
        self._metadata[bucket_id] = {
            "id": bucket_id,
            "name": name,
            "type": type_id,
            "client": client,
            "hostname": hostname,
            "created": created
        }
        self.db[bucket_id] = []

    def delete_bucket(self, bucket_id):
        del self.db[bucket_id]
        del self._metadata[bucket_id]

    def buckets(self):
        buckets = {}
        for bucket_id in self.db:
            buckets[bucket_id] = self.get_metadata(bucket_id)
        return buckets

    def get_events(self, bucket: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        for event in self.db[bucket]:
            pass
        if starttime or endtime:
            raise NotImplementedError
        if limit == -1:
            limit = sys.maxsize
        return self.db[bucket][-limit:]

    def get_metadata(self, bucket_id: str):
        return self._metadata[bucket_id]

    def insert_one(self, bucket: str, event: Event):
        self.db[bucket].append(event)

    def replace_last(self, bucket_id, event):
        self.db[bucket_id][-1] = event


class FileStorageStrategy(StorageStrategy):
    """For storage of data in JSON files, useful as a zero-dependency/databaseless solution"""

    def __init__(self, testing, maxfilesize=10**5):
        self.logger = logger.getChild("file")
        self._fileno = 0
        self._maxfilesize = maxfilesize

        # Create dirs
        self.user_data_dir = appdirs.user_data_dir("aw-server", "activitywatch")
        self.buckets_dir = os.path.join(self.user_data_dir, "testing" if testing else "", "buckets")
        if not os.path.exists(self.buckets_dir):
            os.makedirs(self.buckets_dir)

    def _get_bucket_dir(self, bucket_id):
        return os.path.join(self.buckets_dir, bucket_id)

    def _get_filename(self, bucket_id: str, fileno: int = None):
        bucket_dir = self._get_bucket_dir(bucket_id)
        return os.path.join(bucket_dir, str(self._fileno))

    def create_bucket(self, bucket_id, type_id, client, hostname, created, name=None):
        bucket_dir = self._get_bucket_dir(bucket_id)
        if not os.path.exists(bucket_dir):
            os.makedirs(bucket_dir)
        if not name:
            name = bucket_id
        metadata = {
            "id": bucket_id,
            "name": name,
            "type": type_id,
            "client": client,
            "hostname": hostname,
            "created": created
        }
        with open(os.path.join(bucket_dir, "metadata.json"), "w") as f:
            f.write(json.dumps(metadata))

    def delete_bucket(self, bucket_id):
        rmtree(self._get_bucket_dir(bucket_id))

    def get_events(self, bucket: str, limit: int,
                   starttime: datetime=None, endtime: datetime=None):
        if starttime or endtime:
            raise NotImplementedError
        if limit == -1:
            limit = sys.maxsize
        filename = self._get_filename(bucket)
        if not os.path.isfile(filename):
            return []
        with open(filename) as f:
            # FIXME: I'm slow and memory consuming with large files, see this:
            # https://stackoverflow.com/questions/2301789/read-a-file-in-reverse-order-using-python
            data = [json.loads(line) for line in f.readlines()[-limit:]]
        return data

    def buckets(self):
        buckets = {}
        for bucket_id in os.listdir(self.buckets_dir):
            buckets[bucket_id] = self.get_metadata(bucket_id)
        return buckets

    def get_metadata(self, bucket_id: str):
        metafile = os.path.join(self._get_bucket_dir(bucket_id), "metadata.json")
        with open(metafile, 'r') as f:
            metadata = json.load(f)
        return metadata

    def insert_one(self, bucket: str, event: Event):
        self.insert_many(bucket, [event])

    def insert_many(self, bucket: str, events: Sequence[Event]):
        filename = self._get_filename(bucket)

        # Decide wether to append or create a new file
        """
        if os.path.isfile(filename):
            size = os.path.getsize(filename)
            if size > self._maxfilesize:
                print("Bucket larger than allowed")
                print(size, self._maxfilesize)
        """

        # Option: Limit on events per file instead of filesize
        """
        num_lines = sum(1 for line in open(filename))
        """

        str_to_append = "\n".join([json.dumps(event.to_json_dict()) for event in events])
        with open(filename, "a+") as f:
            f.write(str_to_append + "\n")

    def replace_last(self, bucket, newevent):
        events = self.get_events(bucket, -1)
        filename = self._get_filename(bucket)
        with open(filename, "w") as f:
            events[-1] = newevent.to_json_dict()
            newfiledata = "\n".join([json.dumps(event) for event in events]) + "\n"
            f.write(newfiledata)
