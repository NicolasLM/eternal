from functools import wraps

from urwid.compat import reraise
from urwid.main_loop import EventLoop, ExitMainLoop


class AsyncioEventLoop(EventLoop):
    """
    Event loop based on the standard library ``asyncio`` module.
    ``asyncio`` is new in Python 3.4, but also exists as a backport on PyPI for
    Python 3.3.  The ``trollius`` package is available for older Pythons with
    slightly different syntax, but also works with this loop.
       .. note::
        If you make any changes to the urwid state outside of it
        handling input or responding to alarms (for example, from asyncio.Task
        running in background), and wish the screen to be
        redrawn, you must call :meth:`MainLoop.draw_screen` method of the
        main loop manually.
        A good way to do this::
            asyncio.get_event_loop().call_soon(main_loop.draw_screen)
    """

    _we_started_event_loop = False

    def __init__(self, **kwargs):
        if "loop" in kwargs:
            self._loop = kwargs.pop("loop")
        else:
            import asyncio

            self._loop = asyncio.get_event_loop()

        self._idle_asyncio_handle = None
        self._idle_handle = 0
        self._idle_callbacks = {}

    def _also_call_idle(self, callback):
        """
        Wrap the callback to also call _entering_idle.
        """

        @wraps(callback)
        def wrapper():
            if not self._idle_asyncio_handle:
                self._idle_asyncio_handle = self._loop.call_soon(self._entering_idle)
            return callback()

        return wrapper

    def _entering_idle(self):
        """
        Call all the registered idle callbacks.
        """
        try:
            for callback in self._idle_callbacks.values():
                callback()
        finally:
            self._idle_asyncio_handle = None

    def alarm(self, seconds, callback):
        """
        Call callback() a given time from now.  No parameters are
        passed to callback.
        Returns a handle that may be passed to remove_alarm()
        seconds -- time in seconds to wait before calling callback
        callback -- function to call from event loop
        """
        return self._loop.call_later(seconds, self._also_call_idle(callback))

    def remove_alarm(self, handle):
        """
        Remove an alarm.
        Returns True if the alarm exists, False otherwise
        """
        cancelled = (
            handle.cancelled()
            if getattr(handle, "cancelled", None)
            else handle._cancelled
        )
        existed = not cancelled
        handle.cancel()
        return existed

    def watch_file(self, fd, callback):
        """
        Call callback() when fd has some data to read.  No parameters
        are passed to callback.
        Returns a handle that may be passed to remove_watch_file()
        fd -- file descriptor to watch for input
        callback -- function to call when input is available
        """
        self._loop.add_reader(fd, self._also_call_idle(callback))
        return fd

    def remove_watch_file(self, handle):
        """
        Remove an input file.
        Returns True if the input file exists, False otherwise
        """
        return self._loop.remove_reader(handle)

    def enter_idle(self, callback):
        """
        Add a callback for entering idle.
        Returns a handle that may be passed to remove_enter_idle()
        """
        self._idle_handle += 1
        self._idle_callbacks[self._idle_handle] = callback
        return self._idle_handle

    def remove_enter_idle(self, handle):
        """
        Remove an idle callback.
        Returns True if the handle was removed.
        """
        try:
            del self._idle_callbacks[handle]
        except KeyError:
            return False
        return True

    _exc_info = None

    def _exception_handler(self, loop, context):
        exc = context.get("exception")
        if exc:
            loop.stop()
            if self._idle_asyncio_handle:
                # clean it up to prevent old callbacks
                # from messing things up if loop is restarted
                self._idle_asyncio_handle.cancel()
                self._idle_asyncio_handle = None
            if not isinstance(exc, ExitMainLoop):
                # Store the exc_info so we can re-raise after the loop stops
                self._exc_info = (type(exc), exc, exc.__traceback__)
        else:
            loop.default_exception_handler(context)

    def run(self):
        """
        Start the event loop.  Exit the loop when any callback raises
        an exception.  If ExitMainLoop is raised, exit cleanly.
        """
        self._loop.set_exception_handler(self._exception_handler)
        self._loop.run_forever()
        if self._exc_info:
            exc_info = self._exc_info
            self._exc_info = None
            reraise(*exc_info)
