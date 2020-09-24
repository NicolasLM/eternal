from libirc import (
    parse_message, parse_received, parse_message_tags, parse_message_params,
    parse_message_source, Source, parse_capabilities_ls, get_sasl_plain_payload
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


def test_parse_message_tags():
    assert parse_message_tags('id=123AB;rose') == {'id': '123AB', 'rose': True}
    assert parse_message_tags('url=;netsplit=tur,ty') == {'url': '', 'netsplit': 'tur,ty'}


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
    params = ['*', 'LS', 'multi-prefix sasl=PLAIN,EXTERNAL server-time draft/packing=EX1,EX2']
    assert parse_capabilities_ls(params) == {
        'multi-prefix': True,
        'sasl': 'PLAIN,EXTERNAL',
        'server-time': True,
        'draft/packing': 'EX1,EX2'
    }
    params = ['*', 'LS', '*', 'multi-prefix']
    assert parse_capabilities_ls(params) == {
        'multi-prefix': True
    }


def test_get_sasl_plain_payload():
    assert get_sasl_plain_payload('foo', 'bar') == 'Zm9vAGZvbwBiYXI='
