import logging
from typing import List, Dict, Tuple

from aw_core.models import Event

logger = logging.getLogger(__name__)


def merge_events_by_keys(events, keys) -> List[Event]:
    # The result will be a list of events without timestamp since they are merged
    # Call recursively until all keys are consumed
    if len(keys) < 1:
        return events
    merged_events = {}  # type: Dict[Tuple, Event]
    for event in events:
        composite_key = ()  # type: Tuple
        for key in keys:
            if key in event.data:
                composite_key = composite_key + (event["data"][key],)
        if composite_key not in merged_events:
            merged_events[composite_key] = Event(
                timestamp=event.timestamp,
                duration=event.duration,
                data={}
            )
            for key in keys:
                if key in event.data:
                    merged_events[composite_key].data[key] = event.data[key]
        else:
            merged_events[composite_key].duration += event.duration
    result = []
    for key in merged_events:
        result.append(Event(**merged_events[key]))
    return result
