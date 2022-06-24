import asyncio
from logging import getLogger
from typing import Optional

from . import libirc


logger = getLogger(__name__)


class IRCClientProtocol(asyncio.Protocol):

    def __init__(self, loop: asyncio.AbstractEventLoop, config: dict, irc: libirc.IRCClient):
        self._loop = loop
        self._config = config
        self._irc = irc

        self._transport: Optional[asyncio.Transport] = None

    # Protocol interface

    def connection_made(self, transport):
        self._transport = transport
        addr = (self._config['server'], self._config['port'])
        logger.info('Connected to %s:%d', *addr)

    def data_received(self, data):
        self._irc.add_received_data(data)

    def connection_lost(self, exc):
        logger.info('The server closed the connection')
        self._irc.notify_connection_closed()

    # IRC Client

    def send_bytes(self, data: bytes):
        """Call this with the data to send to the remote server."""
        self._transport.write(data)
