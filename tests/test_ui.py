import urwid

from eternal import ui


def test_convert_formatting():
    expected = []
    assert ui.convert_formatting('') == expected
    assert ui.convert_formatting('\x0F\x0F\x0F') == expected

    expected = [
        (urwid.AttrSpec('default', 'default'), 'No formatting')
    ]
    assert ui.convert_formatting('No formatting') == expected

    expected = [
        (urwid.AttrSpec('default', 'default'), 'Hello'),
        (urwid.AttrSpec('default', 'default'), 'World')
    ]
    assert ui.convert_formatting('Hello\x0FWorld') == expected
    assert ui.convert_formatting('\x0F\x0F\x0FHello\x0FWorld\x0F\x0F') == expected

    expected = [
        (urwid.AttrSpec('default', 'default'), 'Hello'),
        (urwid.AttrSpec('default,italics', 'default'), 'World')
    ]
    assert ui.convert_formatting('Hello\x1DWorld\x1D') == expected
    assert ui.convert_formatting('Hello\x1DWorld') == expected

    expected = [
        (urwid.AttrSpec('default', 'default'), 'Hello'),
        (urwid.AttrSpec('default,bold', 'default'), 'World'),
        (urwid.AttrSpec('default', 'default'), '!'),
    ]
    assert ui.convert_formatting('Hello\x02World\x02!') == expected

    expected = [
        (urwid.AttrSpec('default,underline', 'default'), 'Hello'),
        (urwid.AttrSpec('default,underline,italics', 'default'), 'World')
    ]
    assert ui.convert_formatting('\x1FHello\x1DWorld\x0F') == expected
    assert ui.convert_formatting('\x1FHello\x1DWorld') == expected

    expected = [
        (urwid.AttrSpec('default', 'default'), 'Hello'),
        (urwid.AttrSpec('dark red', 'default'), 'World')
    ]
    assert ui.convert_formatting('Hello\x034World\x03') == expected
    assert ui.convert_formatting('Hello\x034World') == expected

    expected = [
        (urwid.AttrSpec('dark blue,bold', 'light gray'), 'Hello'),
        (urwid.AttrSpec('default', 'default'), 'World')
    ]
    assert ui.convert_formatting('\x02\x032,15Hello\x0FWorld') == expected
