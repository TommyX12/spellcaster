from threading import Timer
import traceback


def get_traceback():
    '''
    Return error traceback in string.
    '''
    return traceback.format_exc()


class RepeatedTimer(object):
    """
    Reference: https://stackoverflow.com/a/38317060
    """

    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_waiting = False

    def _run(self):
        self.is_waiting = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_waiting:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_waiting = True

    def stop(self):
        self._timer.cancel()
        self.is_waiting = False
