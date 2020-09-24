import base64
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import List, Dict, Union, Optional


logger = logging.getLogger(__name__)


def parse_received(recv_buffer: bytearray):
    messages = recv_buffer.split(b'\r\n')
    index = recv_buffer.rfind(b'\r\n')
    if index != -1:
        messages.pop()
        with open('/tmp/received.log', mode='ab') as f:
            f.write(recv_buffer[:index+2])
        recv_buffer[:index+2] = b''
    else:
        with open('/tmp/received.log', mode='ab') as f:
            f.write(recv_buffer)

    for message in messages:
        yield parse_message(message)


def get_utc_now() -> datetime:
    return datetime.utcnow().replace(tzinfo=timezone.utc)


@dataclass
class Source:
    """Represent the source of an event."""
    source: str
    nick: str
    user: str
    host: str

    def __str__(self):
        return self.nick or self.host or self.source


@dataclass
class Message:
    """Represent an IRC message."""
    tags: dict = field(default_factory=dict)
    source: Source = field(default_factory=Source)
    command: str = ''
    params: List[str] = field(default_factory=list)

    time: datetime = field(default_factory=get_utc_now)


@dataclass
class ChannelJoinedEvent(Message):
    """Someone joined a channel."""
    channel: str = ''


@dataclass
class ChannelPartEvent(Message):
    """Someone left a channel."""
    channel: str = ''


@dataclass
class QuitEvent(Message):
    """Someone disconnected from the server."""
    reason: str = ''


@dataclass
class NewMessageEvent(Message):
    """New message from someone."""
    channel: str = ''
    message: str = ''


@dataclass
class ChannelTopicEvent(Message):
    """Channel topic."""
    channel: str = ''
    topic: str = ''


@dataclass
class NickChangedEvent(Message):
    """A user changed its nick."""
    old_nick: str = ''
    new_nick: str = ''


@dataclass
class ChannelNamesEvent(Message):
    """List of nicks in a channel."""
    channel: str = ''
    nicks: List[str] = field(default_factory=list)


@dataclass
class ConnectionClosedEvent:
    """Channel topic."""


class IRCClient:

    def __init__(self, config: dict):
        self._config = config

        self.capabilities: Dict[str, Union[bool, str]] = {}
        self.nick = self._config['nick']

        self._recv_buffer = bytearray()
        self._tmp_channel_nicks: Dict[str, List[str]] = defaultdict(list)
        self._tmp_batches: Dict[str, List[Message]] = dict()

    def add_received_data(self, data: bytes):
        self._recv_buffer.extend(data)
        for msg in parse_received(self._recv_buffer):
            event = self._process_message(msg)
            if event is not None:
                yield event

    def _process_message(self, msg: Message) -> Optional[Message]:
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
            return None

        # Add the current message to an in progress batch if the
        # message carries a batch tag and the batch exists.
        try:
            self._tmp_batches[msg.tags['batch']].append(msg)
        except KeyError:
            pass
        else:
            return None

        if msg.command == 'PING':
            try:
                cookie = msg.params[0]
            except IndexError:
                self.send_to_server('PONG')
            else:
                self.send_to_server(f'PONG :{cookie}')
            return None

        if msg.command == 'JOIN':
            return ChannelJoinedEvent(channel=msg.params[0], **msg.__dict__)

        if msg.command == 'PART':
            return ChannelPartEvent(channel=msg.params[0], **msg.__dict__)

        if msg.command == 'QUIT':
            return QuitEvent(reason=msg.params[0], **msg.__dict__)

        if msg.command == 'NICK':
            if msg.source.nick == self.nick:
                self.nick = msg.params[0]
            return NickChangedEvent(old_nick=str(msg.source), new_nick=msg.params[0], **msg.__dict__)

        if msg.command in ('PRIVMSG', 'NOTICE'):
            destination = msg.params[0]
            if destination == self._config['nick']:
                destination = msg.source.nick or msg.source.host
            return NewMessageEvent(channel=destination, message=msg.params[1], **msg.__dict__)

        if msg.command == '332':
            return ChannelTopicEvent(channel=msg.params[1], topic=msg.params[2], **msg.__dict__)

        if msg.command == '353':
            channel = msg.params[2]
            nicks = msg.params[3].split(' ')
            self._tmp_channel_nicks[channel].extend(nicks)
            return None

        if msg.command == '366':
            channel = msg.params[1]
            return ChannelNamesEvent(
                channel=channel,
                nicks=self._tmp_channel_nicks.pop(channel),
                **msg.__dict__
            )

        if msg.command == 'CAP':
            if msg.params[1] == 'LS':
                self.capabilities.update(parse_capabilities_ls(msg.params))
            # TODO: Handle add and remove capability

        return msg


def parse_message(data: bytearray) -> Message:
    message_str = data.decode(errors='replace')
    if message_str.startswith('@'):
        tags = parse_message_tags(message_str[1:message_str.find(' ')])
        message_str = message_str[message_str.find(' ') + 1:]
    else:
        tags = {}

    if message_str.startswith(':'):
        source = message_str[1:message_str.find(' ')]
        message_str = message_str[message_str.find(' ') + 1:]
    else:
        source = ''
    source = parse_message_source(source)

    space_index = message_str.find(' ')
    if space_index == -1:
        command = message_str.upper()
        params = []
    else:
        command = message_str[:space_index].upper()
        params = parse_message_params(message_str[space_index + 1:])

    try:
        # The time of a message may be included in tags if the
        # server supports the `server-time` capability.
        time = parse_rfc3339_datetime(tags['time'])
    except (KeyError, ValueError):
        time = get_utc_now()

    return Message(tags, source, command, params, time)


def parse_rfc3339_datetime(datetime_str: str) -> datetime:
    """Parse a subset of RFC 3339, also known as ISO 8601:2004(E)."""
    return datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%S.%f%z")


def parse_message_tags(tags: str) -> dict:
    rv = dict()
    for tag in tags.split(';'):
        if '=' in tag:
            key, value = tag.split('=', maxsplit=1)
        else:
            key = tag
            value = True
        rv[key] = value
    return rv


def parse_message_params(params: str) -> List[str]:
    if not params:
        return []

    if params.startswith(':'):
        return [params[1:]]

    index = params.find(' :')
    if index == -1:
        return params.split(' ')

    rv = params[:index].split(' ')
    rv.append(params[index+2:])
    return rv


def parse_message_source(source: str) -> Source:
    if source == '':
        return Source('', '', '', '')

    i_ex = source.find('!')
    i_at = source.find('@')
    if i_ex == -1 or i_at == -1:
        return Source(
            source,
            '',
            '',
            source[i_at+1:],
        )

    return Source(
        source,
        source[:i_ex],
        source[i_ex+1:i_at],
        source[i_at+1:],
    )


def parse_capabilities_ls(cap_params: List[str]) -> Dict[str, Union[bool, str]]:
    rv = dict()
    cap_str = cap_params[-1]
    capabilities = cap_str.split(' ')
    for capability in capabilities:
        if '=' in capability:
            key, value = capability.split('=')
        else:
            key = capability
            value = True
        rv[key] = value

    return rv


def get_sasl_plain_payload(user: str, password: str) -> str:
    return base64.b64encode(f'{user}\00{user}\00{password}'.encode()).decode()
