from .datastore import Datastore


def get_storage_methods():
    from .storage_strategies import FileStorageStrategy, MemoryStorageStrategy, MongoDBStorageStrategy, TinyDbStorage
    methods = [FileStorageStrategy, MemoryStorageStrategy, TinyDbStorage]

    try:
        import pymongo
        methods.append(MongoDBStorageStrategy)
    except ImportError:  # pragma: no cover
        pass

    return methods


def get_storage_method_names():
    methods = get_storage_methods()
    names = [method.__name__[:-15].lower() for method in methods]
    return names
