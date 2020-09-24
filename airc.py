import asyncio
from collections import defaultdict
from contextlib import contextmanager
from logging import getLogger
from typing import Optional, Dict, Union, List

import libirc


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

        #: Queue where parsed messages received from the IRC server are placed
        self.inbox = asyncio.Queue()
        self.capabilities: Dict[str, Union[bool, str]] = {}
        self.nick = self._config['nick']

        self._transport: Optional[asyncio.Transport] = None
        self._recv_buffer = bytearray()
        self._tmp_channel_nicks: Dict[str, List[str]] = defaultdict(list)
        self._tmp_batches: Dict[str, List[libirc.Message]] = dict()
        self.hub = Hub()

    # Protocol interface

    def connection_made(self, transport):
        self._transport = transport
        addr = (self._config['server'], self._config['port'])
        logger.info('Connected to %s:%d', *addr)
        self._loop.create_task(self._negotiate_capabilities())

    def data_received(self, data):
        self._recv_buffer.extend(data)
        for msg in libirc.parse_received(self._recv_buffer):
            self._process_message(msg)
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
        if sasl_config and 'sasl' in self.capabilities:
            self.send_to_server('CAP REQ :sasl')
            self.send_to_server('AUTHENTICATE PLAIN')
            payload = libirc.get_sasl_plain_payload(sasl_config['user'], sasl_config['password'])
            self.send_to_server(f'AUTHENTICATE {payload}')

        if 'echo-message' in self.capabilities:
            self.send_to_server('CAP REQ :echo-message')

        if 'server-time' in self.capabilities:
            self.send_to_server('CAP REQ :server-time')

        if 'batch' in self.capabilities:
            self.send_to_server('CAP REQ :batch')

        self.send_to_server('CAP END')

    def _process_message(self, msg: libirc.Message):
        # Batches allow to put messages on hold and deliver them all at
        # once, a bit like a database transaction.
        # Note that this implementation probably doesn't handle nested
        # batches well.
        if msg.command == 'BATCH':
            if msg.params[0].startswith('+'):
                # Beginning of a new batch
                self._tmp_batches[msg.params[0][1:]] = list()
            elif msg.params[0].startswith('-'):
                # End of a batch
                for batch_msg in self._tmp_batches.pop(msg.params[0][1:], []):
                    self._process_message(batch_msg)
            return

        # Add the current message to an in progress batch if the
        # message carries a batch tag and the batch exists.
        try:
            self._tmp_batches[msg.tags['batch']].append(msg)
        except KeyError:
            pass
        else:
            return

        if msg.command == 'PING':
            try:
                cookie = msg.params[0]
            except IndexError:
                self.send_to_server('PONG')
            else:
                self.send_to_server(f'PONG :{cookie}')
            return

        if msg.command == 'JOIN':
            self.inbox.put_nowait(libirc.ChannelJoinedEvent(channel=msg.params[0], **msg.__dict__))
            return

        if msg.command == 'PART':
            self.inbox.put_nowait(libirc.ChannelPartEvent(channel=msg.params[0], **msg.__dict__))
            return

        if msg.command == 'QUIT':
            self.inbox.put_nowait(libirc.QuitEvent(reason=msg.params[0], **msg.__dict__))
            return

        if msg.command == 'NICK':
            if msg.source.nick == self.nick:
                self.nick = msg.params[0]
            self.inbox.put_nowait(libirc.NickChangedEvent(old_nick=str(msg.source), new_nick=msg.params[0], **msg.__dict__))
            return

        if msg.command in ('PRIVMSG', 'NOTICE'):
            destination = msg.params[0]
            if destination == self._config['nick']:
                destination = msg.source.nick or msg.source.host
            self.inbox.put_nowait(libirc.NewMessageEvent(channel=destination, message=msg.params[1], **msg.__dict__))
            return

        if msg.command == '332':
            self.inbox.put_nowait(libirc.ChannelTopicEvent(channel=msg.params[1], topic=msg.params[2], **msg.__dict__))
            return

        if msg.command == '353':
            channel = msg.params[2]
            nicks = msg.params[3].split(' ')
            self._tmp_channel_nicks[channel].extend(nicks)
            return

        if msg.command == '366':
            channel = msg.params[1]
            self.inbox.put_nowait(libirc.ChannelNamesEvent(
                channel=channel,
                nicks=self._tmp_channel_nicks.pop(channel),
                **msg.__dict__
            ))
            return

        if msg.command == 'CAP':
            if msg.params[1] == 'LS':
                self.capabilities.update(libirc.parse_capabilities_ls(msg.params))
            # TODO: Handle add and remove capability

        self.inbox.put_nowait(msg)
