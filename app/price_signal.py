import asyncio
import asyncpg
import time
from shiny import reactive

price_signal = reactive.Value(0)
_task_started = False
_counter = 0

async def _listen_loop(db_password: str):
    conn = await asyncpg.connect(
        host="localhost", database="assetdb", user="jake", password=db_password
    )
    async def on_notify(conn, pid, channel, payload):
        global _counter
        _counter += 1
        t_recv = time.perf_counter()
        async with reactive.lock():
            price_signal.set(_counter)
            await reactive.flush()
        t_flush = time.perf_counter()
        elapsed_ms = (t_flush - t_recv) * 1000
        print(f"[NOTIFY #{_counter}] recv→flush: {elapsed_ms:.1f}ms", flush=True)

    await conn.add_listener("price_updated", on_notify)
    while True:
        await asyncio.sleep(3600)

def start_signal_listener(db_password: str):
    global _task_started
    if not _task_started:
        _task_started = True
        asyncio.get_event_loop().create_task(_listen_loop(db_password))