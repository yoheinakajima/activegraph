# Testing FalkorDB locally

A quick guide for manually testing the `FalkorDBEventStore` backend on
your development machine.

## Prerequisites

- Python 3.11+
- Docker

## 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

## 2. Install activegraph with the FalkorDB extra

From the repo root:

```bash
pip install -e ".[falkordb]"
```

## 3. Start FalkorDB

```bash
docker run -d --name falkordb -p 6379:6379 falkordb/falkordb:latest
```

## 4. Quick smoke test

```bash
python3 -c "
from activegraph.store import open_store
from activegraph.core.event import Event

store = open_store('falkor://localhost:6379/mytest', run_id='run_001')
store.append(Event(
    id='evt_1', type='test',
    payload={'hi': 'world'},
    actor=None, frame_id=None, caused_by=None,
    timestamp='2026-01-01T00:00:00Z',
))
print('count:', store.count())
print('payload:', store.get_event('evt_1').payload)
store.close()
print('OK')
"
```

Expected output:

```
count: 1
payload: {'hi': 'world'}
OK
```

## 5. Run the conformance suite

```bash
ACTIVEGRAPH_TEST_FALKORDB_URL=falkor://localhost:6379/agtest \
  python3 -m pytest tests/test_falkordb_store.py -v
```

All 9 tests should pass.

## 6. Inspect the graph with redis-cli (optional)

```bash
redis-cli -p 6379
127.0.0.1:6379> GRAPH.QUERY mytest "MATCH (e:Event) RETURN e.id, e.type, e.seq ORDER BY e.seq"
```

## 7. Stop FalkorDB when done

```bash
docker rm -f falkordb
```
