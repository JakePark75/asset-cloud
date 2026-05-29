import asyncio
import asyncpg
from shiny import reactive

price_signal = reactive.Value(0)
_task_started = False

async def _listen_loop(db_password: str):
    conn = await asyncpg.connect(
        host="localhost", database="assetdb", user="jake", password=db_password
    )
    async def on_notify(conn, pid, channel, payload):
        async with reactive.lock():
            price_signal.set(price_signal.get() + 1)
            await reactive.flush()

    await conn.add_listener("price_updated", on_notify)
    while True:
        await asyncio.sleep(3600)

def start_signal_listener(db_password: str):
    global _task_started
    if not _task_started:
        _task_started = True
        asyncio.get_event_loop().create_task(_listen_loop(db_password))