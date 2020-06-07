import asyncio
import json
from multiprocessing import Queue
from typing import Awaitable, Dict, List

import websockets
from google.cloud import error_reporting

from common import config, market_data
from common.tlog import tlog

from .streaming_base import StreamingBase, WSConnectState

NY = "America/New_York"
error_logger = error_reporting.Client()


class FinnhubStreaming(StreamingBase):
    END_POINT = "wss://ws.finnhub.io?token="

    def __init__(
        self, api_key: str, queues: List[Queue], queue_id_hash: Dict[str, int]
    ):
        self.api_key = api_key
        self.state: WSConnectState = WSConnectState.NOT_CONNECTED
        self.websocket: websockets.client.WebSocketClientProtocol
        self.consumer_task: asyncio.Task
        self.queue_id_hash = queue_id_hash
        self.stream_map: Dict = {}
        super().__init__(queues)

    async def connect(self) -> bool:
        """Connect web-socket and authenticate, update internal state"""
        try:
            self.websocket = await websockets.client.connect(
                f"{self.END_POINT}{self.api_key}"
            )
            self.state = WSConnectState.CONNECTED
        except websockets.WebSocketException as wse:
            error_logger.report_exception()
            tlog(f"Exception when connecting to Finnhub WS {wse}")
            self.state = WSConnectState.NOT_CONNECTED
            return False

        self.state = WSConnectState.AUTHENTICATED

        self.consumer_task = asyncio.create_task(
            self._consumer(), name="finnhub-streaming-consumer-task"
        )

        tlog("Successfully connected to Finnhub web-socket")
        return True

    async def close(self) -> None:
        """Close open websocket, if open"""
        if self.state not in (
            WSConnectState.AUTHENTICATED,
            WSConnectState.CONNECTED,
        ):
            raise ValueError("can't close a non-open connection")
        try:
            await self.websocket.close()
        except websockets.WebSocketException as wse:
            tlog(f"failed to close web-socket w exception {wse}")

        self.state = WSConnectState.NOT_CONNECTED

    async def subscribe(self, symbol: str, handler: Awaitable) -> bool:
        if self.state != WSConnectState.AUTHENTICATED:
            raise ValueError(
                f"{symbol} web-socket not ready for listening, make sure connect passed successfully"
            )
        _subscribe_payload = {"type": "subscribe", "symbol": f"{symbol}"}
        await self.websocket.send(json.dumps(_subscribe_payload))
        self.stream_map[symbol] = (handler, self.queue_id_hash[symbol])
        return True

    async def unsubscribe(self, symbol: str) -> bool:
        if self.state != WSConnectState.AUTHENTICATED:
            raise ValueError(
                f"{symbol} web-socket not ready for listening, make sure connect passed successfully"
            )
        _subscribe_payload = {"type": "unsubscribe", "symbol": f"{symbol}"}
        await self.websocket.send(json.dumps(_subscribe_payload))
        self.stream_map.pop(symbol, None)
        return False

    async def _reconnect(self) -> None:
        """automatically reconnect socket, and re-subscribe, internal"""
        tlog(f"{self.consumer_task.get_name()} reconnecting")
        await self.close()
        if await self.connect():
            _dict = self.stream_map.copy()
            self.stream_map.clear()

            for symbol in _dict:
                await self.subscribe(symbol, _dict[symbol])
        else:
            tlog(
                f"{self.consumer_task.get_name()} failed reconnect check log for reason"
            )

    async def _consumer(self) -> None:
        """Main tread loop for consuming incoming messages, internal only """

        tlog(f"{self.consumer_task.get_name()} starting")
        try:
            while True:
                _msg = await self.websocket.recv()
                if isinstance(_msg, bytes):
                    _msg = _msg.decode("utf-8")
                msg = json.loads(_msg)
                stream = msg.get("data")
                for item in stream:
                    try:
                        _func, _q_id = self.stream_map.get(item["s"], None)
                        if _func:
                            await _func(
                                stream["type"], item, self.queues[_q_id]
                            )
                        else:
                            tlog(
                                f"{self.consumer_task.get_name()} received {_msg} to an unknown stream {stream}"
                            )
                    except Exception as e:
                        error_logger.report_exception()
                        tlog(
                            f"{self.consumer_task.get_name()}  exception {e.__class__.__qualname__}:{e}"
                        )

        except websockets.WebSocketException as wse:
            tlog(
                f"{self.consumer_task.get_name()} received WebSocketException {wse}"
            )
            await self._reconnect()
        except asyncio.CancelledError:
            tlog(f"{self.consumer_task.get_name()} cancelled")

        tlog(f"{self.consumer_task.get_name()} completed")

    @classmethod
    async def handler(cls, event: str, data: Dict, queue: Queue) -> None:
        print(f"{event}: {data}")