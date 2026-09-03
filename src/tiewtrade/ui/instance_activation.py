"""The doorbell a second launch rings on the window already open.

One installation runs one Bot Session, and `InstallationLock` already refuses
the second process. Refusing is not enough on its own: a trader who clicks the
icon again is asking for their window, and being told "it is open somewhere"
leaves them to go and find it.

So the process that holds the installation listens on a local socket, and a
process that could not have it rings that socket instead of opening anything.
Qt carries this on the platform's own local IPC — a Unix domain socket on
macOS and Linux, a named pipe on Windows — so there is no dependency here
beyond the one the UI already has.

Nothing about trading passes through this channel. The message means only
"someone asked for the window", and the window is the only thing that answers
it.
"""

from __future__ import annotations

import getpass
import logging
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket

_logger = logging.getLogger(__name__)

# The message body is never read for content — a connection arriving *is* the
# request. It is sent anyway so the first process sees a readable socket
# rather than an empty one, which is what tells a half-open connection from a
# real ask.
_ACTIVATION_REQUEST = b"activate\n"

_NAME_PREFIX = "tiewtrade"
# Qt puts this on a filesystem path on Unix and in the pipe namespace on
# Windows, both of which have length limits well under what a full path would
# need. A prefix of the digest is short, stable, and collides no more often
# than the installations one machine could hold.
_DIGEST_LENGTH = 16


def activation_name_for(database_path: Path | str) -> str:
    """The name this installation's window answers on.

    Derived from the database rather than from the executable, because the
    database is what "one installation" means here — every Basket, Fill and
    PnL row is bound to it. A checkout pointed at its own file by
    `TIEWTRADE_DATABASE_PATH` is a different installation by that measure, and
    must not ring the installed app's window.

    The user is in the name because on Unix Qt keeps the socket in a directory
    shared between everyone logged in: without it, the first trader to launch
    would own the name for the whole machine and the second would be handed
    their window. One instance per user session, which is what was asked for.
    """
    installation = str(Path(database_path).expanduser().resolve())
    digest = sha256(installation.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    return f"{_NAME_PREFIX}-{digest}-{_user_digest()}"


def _user_digest() -> str:
    """Which logged-in user this is, without putting their name in a path.

    Hashed rather than spelled out because the name lands in a world-readable
    directory on Unix, and because a login name may hold characters a socket
    path or a pipe name would not take.
    """
    try:
        user = getpass.getuser()
    except Exception:
        # Every documented failure here is "there is no account to name" — no
        # environment, no password entry. A single instance per machine is
        # then the honest reading, so every launch shares one name.
        user = ""
    return sha256(user.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


class InstanceActivation:
    """Either end of the doorbell: the one that listens, or the one that rings.

    Both ends are here because they are one protocol, and a name that only one
    half knew how to form would be a protocol waiting to drift. A given
    instance uses one end or the other, never both — which end it is is
    decided by the installation lock, not here.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._server: QLocalServer | None = None
        self._on_request: Callable[[], None] | None = None

    @property
    def name(self) -> str:
        return self._name

    def listen(self, on_request: Callable[[], None]) -> bool:
        """Start answering, and say whether the doorbell is now hung.

        `removeServer` first, because a process that is killed runs no cleanup
        and on Unix leaves its socket file behind — a leftover that would
        otherwise refuse this launch its own name forever. Deleting it is safe
        for one reason only: the caller holds the installation lock, so no
        other process can be running this installation, so nothing live is
        being cut off. Calling this without that lock held would take the name
        out from under a window that is still open.
        """
        if self._server is not None:
            return True
        QLocalServer.removeServer(self._name)
        server = QLocalServer()
        # The socket sits in a shared directory on Unix. Without this, any
        # account on the machine could ring a trader's window.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._someone_asked)
        if not server.listen(self._name):
            _logger.warning(
                "could not listen for activation requests: %s",
                server.errorString(),
                extra={"event_name": "desktop.activation.unavailable"},
            )
            return False
        self._server = server
        self._on_request = on_request
        return True

    def ask_the_running_window(self, *, timeout_ms: int) -> bool:
        """Ring the doorbell, and say whether anyone answered.

        `False` is not an error — it is the answer that there is no window to
        hand over to, which the caller has its own reply for. It covers both
        the leftover socket of a crashed process and the moment before a
        starting instance has begun listening.
        """
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(timeout_ms):
            return False
        try:
            socket.write(_ACTIVATION_REQUEST)
            # A process that exits the instant it has written leaves the bytes
            # in its own buffer and the other window unmoved, so the send has
            # to have left this process before the caller may act on `True`.
            if not socket.waitForBytesWritten(timeout_ms):
                return False
        finally:
            socket.disconnectFromServer()
        return True

    def close(self) -> None:
        """Take the doorbell down. Closing what never listened is not an error.

        The socket file is left where it is, for the same reason the lock file
        is: unlinking a name is only ever safe for the process that has proved
        it owns the installation, and the next launch to prove that will clear
        it in `listen`.
        """
        server = self._server
        self._server = None
        self._on_request = None
        if server is None:
            return
        server.close()

    def _someone_asked(self) -> None:
        while self._server is not None:
            connection = self._server.nextPendingConnection()
            if connection is None:
                return
            self._read_the_request(connection)

    def _read_the_request(self, connection: QLocalSocket) -> None:
        """Hear one ask out, then let the window answer it.

        The bytes are not inspected: this channel carries one message and
        arriving is the whole of it. They are waited for anyway, so that a
        connection which never says anything — a port scan, a probe, a socket
        opened by something that is not this app — cannot raise a trader's
        window.
        """
        connection.disconnected.connect(connection.deleteLater)
        asked = connection.bytesAvailable() > 0 or connection.waitForReadyRead(
            _REQUEST_TIMEOUT_MS
        )
        connection.readAll()
        connection.disconnectFromServer()
        if not asked:
            return
        on_request = self._on_request
        if on_request is not None:
            on_request()


# The sender is a local process that writes immediately and the two are on one
# machine, so this is a bound on a request that has gone wrong rather than a
# wait anything is expected to reach.
_REQUEST_TIMEOUT_MS = 1_000
