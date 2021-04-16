import base64
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import enum
import logging
from typing import List, Dict, Union, Iterable, Optional, Tuple, Set


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


class Numeric(enum.Enum):

    RPL_WELCOME = '001'
    RPL_YOURHOST = '002'
    RPL_CREATED = '003'
    RPL_MYINFO = '004'
    RPL_ISUPPORT = '005'


@dataclass
class Source:
    """Represent the source of an event."""
    source: str = ''
    nick: str = ''
    user: str = ''
    host: str = ''

    def __str__(self):
        return self.nick or self.host or self.source


@dataclass
class User:
    """Represent a user on a network."""
    source: Source = field(default_factory=Source)
    modes: str = ''
    is_away: bool = False
    last_message_at: Optional[datetime] = None

    @property
    def is_recently_active(self) -> bool:
        if self.last_message_at is None:
            return False

        return (get_utc_now() - self.last_message_at) < timedelta(minutes=15)


@dataclass
class Member:
    """Represent a user in a specific channel."""
    user: User = field(default_factory=User)
    prefixes: str = ''


@dataclass
class Channel:
    """Represent a channel on a network."""
    name: str = ''
    modes: str = ''
    topic: str = ''
    members: Dict[str, Member] = field(default_factory=dict)


@dataclass
class Message:
    """Represent an IRC message."""
    tags: dict = field(default_factory=dict)
    source: Source = field(default_factory=Source)
    command: str = ''
    params: List[str] = field(default_factory=list)

    time: datetime = field(default_factory=get_utc_now)

    def to_bytes(self) -> bytes:
        # TODO: This is very incomplete
        rv = self.command
        params = self.params.copy()
        try:
            params[-1] = ':' + params[-1]
        except IndexError:
            pass
        else:
            rv += f" {' '.join(params)}"
        return rv.encode()


@dataclass
class ClientMessage(Message):
    """Represent an IRC from this client to the server."""


@dataclass
class ChannelJoinedEvent(Message):
    """Someone joined a channel."""
    channel: str = ''
    user: User = field(default_factory=User)


@dataclass
class ChannelPartEvent(Message):
    """Someone left a channel."""
    channel: str = ''
    user: User = field(default_factory=User)


@dataclass
class QuitEvent(Message):
    """Someone disconnected from the server."""
    channel: str = ''
    user: User = field(default_factory=User)
    reason: str = ''


@dataclass
class NickChangedEvent(Message):
    """A user changed its nick."""
    channel: str = ''
    user: User = field(default_factory=User)
    old_nick: str = ''
    new_nick: str = ''


@dataclass
class NewMessageEvent(Message):
    """New message from someone."""
    channel: str = ''
    message: str = ''


@dataclass
class NewMessageFromServerEvent(Message):
    """New message from the server."""
    message: str = ''


@dataclass
class ChannelTopicEvent(Message):
    """Channel topic."""
    channel: str = ''
    topic: str = ''


@dataclass
class ChannelTopicWhoTimeEvent(Message):
    """Channel topic."""
    channel: str = ''
    set_by: Source = field(default_factory=Source)
    set_at: datetime = field(default_factory=lambda: datetime(tzinfo=timezone.utc))


@dataclass
class ChannelNamesEvent(Message):
    """List of nicks in a channel."""
    channel: str = ''
    nicks: List[str] = field(default_factory=list)


@dataclass
class ConnectionClosedEvent:
    """Channel topic."""


class UserDefaultDict(defaultdict):

    def __missing__(self, key: str):
        self[key] = User(source=Source(nick=key))
        return self[key]


class IRCClient:

    def __init__(self, config: dict):
        self._config = config

        self.capabilities: Dict[str, Union[bool, str]] = {}
        self.supported: Dict[str, str] = {}
        self.member_prefixes: Dict[str, str] = {}
        self.channel_modes: Dict[str, str] = {}
        self.name = config['server']
        self.nick = self._config['nick']
        self.channels: Dict[str, Channel] = dict()
        self.users: Dict[str, User] = UserDefaultDict()

        self._recv_buffer = bytearray()
        self._tmp_channel_nicks: Dict[str, List[str]] = defaultdict(list)
        self._tmp_batches: Dict[str, List[Message]] = dict()
        self._tmp_motd: List[str] = list()

    def add_received_data(self, data: bytes):
        self._recv_buffer.extend(data)
        for msg in parse_received(self._recv_buffer):
            for processed_msg in self._process_message(msg):
                yield processed_msg

    def _process_message(self, msg: Message) -> List[Message]:
        # Batches allow to put messages on hold and deliver them all at
        # once, a bit like a database transaction.
        # Note that this implementation probably doesn't handle nested
        # batches well.
        if msg.command == 'BATCH':
            rv = list()
            if msg.params[0].startswith('+'):
                # Beginning of a new batch
                self._tmp_batches[msg.params[0][1:]] = list()
            elif msg.params[0].startswith('-'):
                # End of a batch
                for batch_msg in self._tmp_batches.pop(msg.params[0][1:], []):
                    rv.extend(self._process_message(batch_msg))
            return rv

        # Add the current message to an in progress batch if the
        # message carries a batch tag and the batch exists.
        try:
            self._tmp_batches[msg.tags['batch']].append(msg)
        except KeyError:
            pass
        else:
            return []

        try:
            method = getattr(self, f'_process_{msg.command.lower()}_message')
        except AttributeError:
            pass
        else:
            return method(msg)

        if msg.command in ('PRIVMSG', 'NOTICE'):
            destination = msg.params[0]
            if destination == self._config['nick']:
                destination = msg.source.nick or msg.source.host

            self.users[msg.source.nick].last_message_at = get_utc_now()

            return [NewMessageEvent(channel=destination, message=msg.params[1], **msg.__dict__)]

        if msg.command in ('001', '002', '003', '004'):
            message = ' '.join(msg.params[1:])
            return [NewMessageFromServerEvent(message=message, **msg.__dict__)]

        if msg.command == '005':
            supported, not_supported = parse_supported(msg.params)
            self.supported.update(supported)
            for ns in not_supported:
                self.supported.pop(ns, None)

            try:
                prefix = self.supported['PREFIX']
            except KeyError:
                pass
            else:
                self.member_prefixes = parse_member_prefixes(prefix)

            try:
                network = self.supported['NETWORK']
            except KeyError:
                pass
            else:
                if network:
                    self.name = network

            try:
                chanmodes = self.supported['CHANMODES']
            except KeyError:
                pass
            else:
                self.channel_modes = parse_chanmodes(chanmodes)

            return []

        if msg.command == '375':
            self._tmp_motd = list()
            return []

        if msg.command == '372':
            self._tmp_motd.append(
                NewMessageFromServerEvent(message=msg.params[1], **msg.__dict__)
            )
            return []

        if msg.command == '376':
            motd = self._tmp_motd
            self._tmp_motd = list()
            return motd

        if msg.command == '353':
            channel = msg.params[2]
            nicks = msg.params[3].split(' ')
            self._tmp_channel_nicks[channel].extend(nicks)
            return []

        if msg.command == '366':
            channel = msg.params[1]
            nicks = self._tmp_channel_nicks.pop(channel)

            def _member_from_nick(nick: str) -> Member:
                i = 0
                for i, symbol in enumerate(nick):
                    if symbol not in self.member_prefixes.values():
                        break
                prefixes = nick[:i]
                nick = nick[i:]
                user = self.users[nick]

                return Member(user, prefixes=prefixes)

            members = [_member_from_nick(nick) for nick in nicks]
            self.channels[channel].members = {
                m.user.source.nick: m
                for m in members
            }

            return [ChannelNamesEvent(
                channel=channel,
                nicks=nicks,
                **msg.__dict__
            )]

        if msg.command == 'CAP':
            if msg.params[1] == 'LS':
                self.capabilities.update(parse_capabilities_ls(msg.params))
            # TODO: Handle add and remove capability

        return [msg]

    def _process_ping_message(self, msg: Message):
        return [ClientMessage(command='PONG', params=msg.params)]

    def _process_join_message(self, msg: Message):
        channel_name = msg.params[0]
        if msg.source.nick == self.nick and channel_name not in self.channels:
            self.channels[channel_name] = Channel(name=channel_name)

        user = self.users[msg.source.nick]
        self.channels[channel_name].members[user.source.nick] = Member(user)

        return [ChannelJoinedEvent(channel=channel_name, user=user, **msg.__dict__)]

    def _process_part_message(self, msg: Message):
        channel_name = msg.params[0]
        if msg.source.nick == self.nick and channel_name in self.channels:
            del self.channels[channel_name]
        else:
            self.channels[channel_name].members.pop(msg.source.nick)

        user = self.users[msg.source.nick]

        return [ChannelPartEvent(channel=channel_name, user=user, **msg.__dict__)]

    def _process_quit_message(self, msg: Message):
        # Generate an individual quit event for each channel a user was in
        rv = list()
        for channel in self.channels.values():
            member = channel.members.pop(msg.source.nick, None)
            if member is not None:
                rv.append(QuitEvent(
                    channel=channel.name,
                    user=member.user,
                    reason=msg.params[0],
                    **msg.__dict__
                ))

        return rv

    def _process_nick_message(self, msg: Message):
        old_nick = msg.source.nick
        new_nick = msg.params[0]
        if msg.source.nick == self.nick:
            self.nick = new_nick

        user = self.users[old_nick]
        del self.users[old_nick]
        user.source.source.replace(old_nick + '!', new_nick + '!')
        user.source.nick = new_nick
        self.users[new_nick] = user

        # Generate an individual event for each channel a user is in
        rv = list()
        for channel in self.channels.values():
            if old_nick in channel.members:
                member = channel.members.pop(old_nick)
                channel.members[new_nick] = member
                rv.append(NickChangedEvent(
                    channel=channel.name,
                    user=user,
                    old_nick=old_nick,
                    new_nick=new_nick,
                    **msg.__dict__
                ))

        return rv

    def _process_mode_message(self, msg: Message):
        rv = []
        target, modestring, *args = msg.params

        if target in self.channels:
            channel = self.channels[target]
            for is_add, mode, arg in self._iter_modestring(modestring, args, True):
                if mode in self.member_prefixes:
                    if is_add:
                        channel.members[arg].prefixes += self.member_prefixes[mode]
                    else:
                        channel.members[arg].prefixes.replace(self.member_prefixes[mode], '')
                    rv.append(ChannelNamesEvent(
                        channel=channel.name,
                        nicks=[],
                        **msg.__dict__
                    ))
                else:
                    if is_add and mode not in channel.modes:
                        channel.modes += mode
                    else:
                        channel.modes.replace(mode, '')

        elif target in self.users:
            user = self.users[target]
            for is_add, mode, _ in self._iter_modestring(modestring, args, False):
                if is_add:
                    user.modes += mode
                else:
                    user.modes.replace(mode, '')

        else:
            logger.warning('Received a MODE message for a target that does not exist')

        return rv

    def _process_221_message(self, msg: Message):
        """RPL_UMODEIS gives the current modes of the connected client."""
        self.users[msg.params[0]].modes = msg.params[1][1:]
        return []

    def _process_332_message(self, msg: Message):
        channel_name, topic = msg.params[1], msg.params[2]
        try:
            self.channels[channel_name].topic = topic
        except KeyError:
            pass

        return [ChannelTopicEvent(channel=channel_name, topic=topic, **msg.__dict__)]

    def _process_333_message(self, msg: Message):
        channel_name, who, date = msg.params[1], msg.params[2], msg.params[3]
        return [ChannelTopicWhoTimeEvent(
            channel=channel_name,
            set_by=parse_message_source(who),
            set_at=datetime.fromtimestamp(int(date), tz=timezone.utc),
            **msg.__dict__
        )]

    def sort_members_by_prefix(self, members: Iterable[Member]) -> List[Member]:
        """Sort members of a channel.

        Equivalent to ORDER BY prefix, nick.
        """
        member_prefixes_symbols = list(self.member_prefixes.values())
        return sorted(
            members,
            key=lambda m: (
                [str(member_prefixes_symbols.index(c)) for c in m.prefixes] or ['z']
                + list(m.user.source.nick.lower())
            )
        )

    def _iter_modestring(self, modestring: str, args: list, is_channel: bool):
        is_add = True
        args_i = 0
        for m in modestring:
            if m == '+':
                is_add = True
            elif m == '-':
                is_add = False
            elif not is_channel:
                yield is_add, m, None
            elif m in self.member_prefixes:
                yield is_add, m, args[args_i]
                args_i += 1
            elif self.channel_modes[m] in ('A', 'B'):
                yield is_add, m, args[args_i]
                args_i += 1
            elif self.channel_modes[m] == 'C' and is_add:
                yield is_add, m, args[args_i]
                args_i += 1
            else:
                yield is_add, m, None


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


# Tag values can contain escaped characters:
# https://ircv3.net/specs/extensions/message-tags#escaping-values
# Unlike the spec, this approach does not work with extraneous lone '\'.
TAG_VALUE_ESCAPE = {
    r'\:': ';',
    r'\s': ' ',
    r'\\': '\\',
    r'\r': '\r',
    r'\n': '\n'
}


def parse_message_tags(tags: str) -> Dict[str, str]:
    rv = dict()
    for tag in tags.split(';'):
        if '=' in tag:
            key, value = tag.split('=', maxsplit=1)
            if value.endswith('\\'):
                value = value[:-1]
            for escaped, actual in TAG_VALUE_ESCAPE.items():
                value = value.replace(escaped, actual)
        else:
            key = tag
            value = ''
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
            key, value = capability.split('=', maxsplit=1)
        else:
            key = capability
            value = True
        rv[key] = value

    return rv


def get_sasl_plain_payload(user: str, password: str) -> str:
    return base64.b64encode(f'{user}\00{user}\00{password}'.encode()).decode()


def parse_supported(params: List[str]) -> Tuple[Dict[str, str], Set[str]]:
    supported = dict()
    not_supported = set()
    for param in params[1:-1]:
        if '=' in param:
            key, value = param.split('=', maxsplit=1)
        else:
            key = param
            value = ''

        if key.startswith('-'):
            not_supported.add(key[1:])
        else:
            supported[key] = value

    return supported, not_supported


def parse_member_prefixes(prefixes: str) -> Dict[str, str]:
    rv = dict()
    if prefixes == '':
        return rv

    letters, symbols = prefixes.split(')')
    letters = letters[1:]
    for key, value in zip(letters, symbols):
        rv[key] = value
    return rv


def parse_chanmodes(chanmodes: str) -> Dict[str, str]:
    rv = dict()
    mode_types = 'ABCDEFGHIJKLM'
    mode_type_i = 0
    for mode in chanmodes:
        if mode == ',':
            mode_type_i += 1
        else:
            rv[mode] = mode_types[mode_type_i]
    return rv
