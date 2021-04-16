from eternal.libirc import (
    parse_message, parse_received, parse_message_tags, parse_message_params,
    parse_message_source, Source, parse_capabilities_ls, get_sasl_plain_payload,
    Message, IRCClient, Member, User, parse_supported, parse_chanmodes,
    parse_member_prefixes
)


def test_parse_received():
    recv_buffer = bytearray(b'FOO\r\nBAR\r\nBAZ')
    messages = list(parse_received(recv_buffer))
    assert len(messages) == 2
    assert messages[0].command == 'FOO'
    assert messages[1].command == 'BAR'
    assert recv_buffer == bytearray(b'BAZ')


def test_parse_message():
    msg = parse_message(bytearray(b':dan!d@localhost PRIVMSG Foo bar'))
    assert msg.tags == {}
    assert msg.source == Source('dan!d@localhost', 'dan', 'd', 'localhost')
    assert msg.command == 'PRIVMSG'
    assert msg.params == ['Foo', 'bar']


def test_message_to_bytes():
    msg = Message(command='PING')
    assert msg.to_bytes() == b'PING'

    msg = Message(command='PRIVMSG', params=['#chan', 'Hello there'])
    assert msg.to_bytes() == b'PRIVMSG #chan :Hello there'


def test_parse_message_tags():
    assert parse_message_tags('id=123AB;rose') == {'id': '123AB', 'rose': ''}
    assert parse_message_tags('url=;netsplit=tur,ty') == {'url': '', 'netsplit': 'tur,ty'}

    # Test escaping
    assert parse_message_tags(r'a=hello\:there') == {'a': 'hello;there'}
    assert parse_message_tags(r'a=hello\sthere') == {'a': 'hello there'}
    assert parse_message_tags(r'a=\r\n') == {'a': '\r\n'}
    assert parse_message_tags(r'a=/!\\') == {'a': '/!\\'}
    assert parse_message_tags('a=a\\') == {'a': 'a'}


def test_parse_message_params():
    assert parse_message_params('') == []
    assert parse_message_params(':') == ['']
    assert parse_message_params('* LIST :') == ['*', 'LIST', '']
    assert parse_message_params('* LS :multi-prefix sasl') == ['*', 'LS', 'multi-prefix sasl']
    assert parse_message_params('REQ :sasl message-tags foo') == ['REQ', 'sasl message-tags foo']
    assert parse_message_params('#chan :Hey!') == ['#chan', 'Hey!']
    assert parse_message_params('#chan Hey!') == ['#chan', 'Hey!']
    assert parse_message_params(':Hey!') == ['Hey!']


def test_parse_message_source():
    assert parse_message_source('irccat42!~irccat@user-5-184-62-53.internet.com') == Source(
        'irccat42!~irccat@user-5-184-62-53.internet.com',
        'irccat42',
        '~irccat',
        'user-5-184-62-53.internet.com'
    )
    assert parse_message_source('cherryh.freenode.net') == Source(
        'cherryh.freenode.net',
        '',
        '',
        'cherryh.freenode.net'
    )


def test_parse_capabilities_ls():
    params = [
        '*',
        'LS',
        'multi-prefix sasl=PLAIN,EXTERNAL server-time draft/packing=EX1,EX2 '
        'draft/multiline=max-bytes=4096,max-lines=24'
    ]
    assert parse_capabilities_ls(params) == {
        'multi-prefix': True,
        'sasl': 'PLAIN,EXTERNAL',
        'server-time': True,
        'draft/packing': 'EX1,EX2',
        'draft/multiline': 'max-bytes=4096,max-lines=24'
    }
    params = ['*', 'LS', '*', 'multi-prefix']
    assert parse_capabilities_ls(params) == {
        'multi-prefix': True
    }


def test_parse_supported():
    params = [
        'foo',
        'ELIST=CTU',
        '-WHOX',
        'KNOCK',
        'MONITOR=100',
        'are supported by this server'
    ]
    supported, not_supported = parse_supported(params)
    assert supported == {
        'ELIST': 'CTU',
        'KNOCK': '',
        'MONITOR': '100'
    }
    assert not_supported == {'WHOX'}


def test_get_sasl_plain_payload():
    assert get_sasl_plain_payload('foo', 'bar') == 'Zm9vAGZvbwBiYXI='


def test_sort_members():
    m1 = Member(User(source=Source(nick='op')), prefixes='@')
    m2 = Member(User(source=Source(nick='bar')), prefixes='')
    m3 = Member(User(source=Source(nick='baz')), prefixes='')
    m4 = Member(User(source=Source(nick='admin')), prefixes='!')
    m5 = Member(User(source=Source(nick='voiced')), prefixes='+')
    m6 = Member(User(source=Source(nick='1user')), prefixes='')
    m7 = Member(User(source=Source(nick='Adel')), prefixes='')
    m8 = Member(User(source=Source(nick='abel')), prefixes='')
    members = [m2, m4, m8, m1, m6, m3, m7, m5]

    irc = IRCClient({'nick': 'nick', 'server': 'server'})
    irc.member_prefixes = parse_member_prefixes('(Yqaohv)!~&@%+')
    assert irc.sort_members_by_prefix(members) == [m4, m1, m5, m6, m8, m7, m2, m3]


def test_parse_member_prefixes():
    assert parse_member_prefixes('') == {}
    assert parse_member_prefixes('(ov)@+') == {'o': '@', 'v': '+'}
    assert parse_member_prefixes('(ohv)@%+') == {'o': '@', 'h': '%', 'v': '+'}


def test_parse_chanmodes():
    assert parse_chanmodes('') == {}
    assert parse_chanmodes('b,k,l,imn') == {
        'b': 'A',
        'k': 'B',
        'l': 'C',
        'i': 'D',
        'm': 'D',
        'n': 'D'
    }
    assert parse_chanmodes(',,,imn') == {
        'i': 'D',
        'm': 'D',
        'n': 'D'
    }
