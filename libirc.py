import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import List, Dict, Union


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
