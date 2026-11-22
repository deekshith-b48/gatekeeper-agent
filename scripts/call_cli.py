"""Hold a real voice call from your terminal: mic -> WebSocket -> agent -> speaker.
   pip install '.[cli]'   then:   python scripts/call_cli.py --owner owner [--url ws://localhost:8000] [--caller-id me@example.com]
   Use headphones (the mic is muted while the agent speaks, but echo still hurts VAD)."""
import argparse
import asyncio
import json
import sys

import numpy as np
import sounddevice as sd
import websockets

SR, BLOCK = 16000, 512


async def main(a):
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    loop = asyncio.get_running_loop()
    speaking = asyncio.Event()

    def on_audio(indata, frames, t, status):
        if not speaking.is_set():
            loop.call_soon_threadsafe(lambda b=bytes(indata): q.full() or q.put_nowait(b))

    url = f"{a.url}/ws/call/{a.owner}?caller_id={a.caller_id}" + (f"&token={a.token}" if a.token else "")
    async with websockets.connect(url, max_size=None) as ws:
        async def send_mic():
            with sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK, callback=on_audio):
                while True:
                    await ws.send(await q.get())

        async def recv():
            meta = None
            async for m in ws:
                if isinstance(m, bytes):
                    speaking.set()
                    sd.play(np.frombuffer(m, np.int16), meta["sample_rate"])
                    await asyncio.to_thread(sd.wait)
                    while not q.empty():
                        q.get_nowait()  # drop anything captured while speaking
                    speaking.clear()
                    continue
                e = json.loads(m)
                t = e["type"]
                if t == "agent_audio":
                    meta = e
                elif t == "agent_text":
                    print(f"\nAgent: {e['text']}   {e.get('timings', {})}")
                elif t == "transcript_final":
                    print(f"You:   {e['text']}")
                elif t == "transcript_partial":
                    print(f"  ...  {e['text']}", end="\r")
                else:
                    print(f"[{t}] {json.dumps({k: v for k, v in e.items() if k != 'type'})}")

        await asyncio.gather(send_mic(), recv())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://localhost:8000")
    p.add_argument("--owner", default="owner")
    p.add_argument("--caller-id", default="cli-caller")
    p.add_argument("--token", default="")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        sys.exit(0)
