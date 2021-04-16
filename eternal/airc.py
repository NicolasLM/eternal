import asyncio
from contextlib import contextmanager
from logging import getLogger
from typing import Optional

from . import libirc


logger = getLogger(__name__)


class Hub:

    def __init__(self):
        self._subscriptions = set()

    def publish(self, message):
        for queue in self._subscriptions:
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self):
        queue = asyncio.Queue()
        try:
            self._subscriptions.add(queue)
            yield queue
        finally:
            self._subscriptions.remove(queue)


class IRCClientProtocol(asyncio.Protocol):

    def __init__(self, loop: asyncio.AbstractEventLoop, config: dict):
        self._loop = loop
        self._config = config

        self.irc = libirc.IRCClient(self._config)

        #: Queue where parsed messages received from the IRC server are placed
        self.inbox = asyncio.Queue()

        self._transport: Optional[asyncio.Transport] = None
        self.hub = Hub()

    # Protocol interface

    def connection_made(self, transport):
        self._transport = transport
        addr = (self._config['server'], self._config['port'])
        logger.info('Connected to %s:%d', *addr)
        self._loop.create_task(self._negotiate_capabilities())

    def data_received(self, data):
        for msg in self.irc.add_received_data(data):
            if isinstance(msg, libirc.ClientMessage):
                self.send_message_to_server(msg)
            else:
                self.inbox.put_nowait(msg)
                self.hub.publish(msg)

    def connection_lost(self, exc):
        logger.info('The server closed the connection')
        self.inbox.put_nowait(libirc.ConnectionClosedEvent())

    # IRC Client

    def send_to_server(self, line: str):
        payload = line.encode() + b'\r\n'
        with open('/tmp/received.log', mode='ab') as f:
            f.write(payload)
        self._transport.write(payload)

    def send_message_to_server(self, msg: libirc.ClientMessage):
        payload = msg.to_bytes() + b'\r\n'
        with open('/tmp/received.log', mode='ab') as f:
            f.write(payload)
        self._transport.write(payload)

    async def _negotiate_capabilities(self):
        with self.hub.subscribe() as sub:
            self.send_to_server('CAP LS 302')
            self.send_to_server(f'NICK {self._config["nick"]}')
            self.send_to_server(f'USER {self._config["user"]} 0 * :{self._config["real_name"]}')
            while True:
                msg: libirc.Message = await sub.get()
                if msg.command == 'CAP' and msg.params[1] == 'LS' and len(msg.params) == 3:
                    break

        sasl_config = self._config.get('sasl')
        if sasl_config and 'sasl' in self.irc.capabilities:
            self.send_to_server('CAP REQ :sasl')
            self.send_to_server('AUTHENTICATE PLAIN')
            payload = libirc.get_sasl_plain_payload(sasl_config['user'], sasl_config['password'])
            self.send_to_server(f'AUTHENTICATE {payload}')

        if 'message-tags' in self.irc.capabilities:
            self.send_to_server('CAP REQ :message-tags')

        if 'echo-message' in self.irc.capabilities:
            self.send_to_server('CAP REQ :echo-message')

        if 'server-time' in self.irc.capabilities:
            self.send_to_server('CAP REQ :server-time')

        if 'batch' in self.irc.capabilities:
            self.send_to_server('CAP REQ :batch')

        self.send_to_server('CAP END')

        for channel in self._config.get('channels', []):
            self.send_to_server(f'JOIN {channel}')
