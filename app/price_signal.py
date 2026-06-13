import asyncio
import time
import redis.asyncio as redis
from shiny import reactive

price_signal = reactive.Value(0)
daily_insert_signal = reactive.Value(0)

_task_started = False
_price_counter = 0
_insert_counter = 0


async def _listen_loop():
    r = redis.Redis(host="127.0.0.1", port=6379, db=0)

    async with r.pubsub() as pubsub:
        await pubsub.subscribe("price_updated", "daily_inserted")

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            channel = message["channel"].decode()

            if channel == "price_updated":
                global _price_counter
                _price_counter += 1
                t_recv = time.perf_counter()
                async with reactive.lock():
                    price_signal.set(_price_counter)
                    await reactive.flush()
                t_flush = time.perf_counter()
                elapsed_ms = (t_flush - t_recv) * 1000
                print(f"[price_updated #{_price_counter}] recv→flush: {elapsed_ms:.1f}ms", flush=True)

            elif channel == "daily_inserted":
                global _insert_counter
                _insert_counter += 1
                t_recv = time.perf_counter()
                async with reactive.lock():
                    daily_insert_signal.set(_insert_counter)
                    await reactive.flush()
                t_flush = time.perf_counter()
                elapsed_ms = (t_flush - t_recv) * 1000
                print(f"[daily_inserted #{_insert_counter}] recv→flush: {elapsed_ms:.1f}ms", flush=True)


def start_signal_listener():
    global _task_started
    if not _task_started:
        _task_started = True
        asyncio.get_event_loop().create_task(_listen_loop())