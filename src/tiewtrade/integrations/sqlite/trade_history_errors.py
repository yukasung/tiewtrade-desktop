"""What the Trade History adapter refuses, and what it could not reach.

A conflict is the adapter saying no: the row asked for would contradict one
already written, and writing it would leave two accounts of the same trade.
Unavailable is the adapter saying it could not tell — the database did not
answer, so nothing is claimed either way.

They live apart from the adapter because the rules that raise them do.
"""


class TradeHistoryError(RuntimeError):
    """Something went wrong with the Trade History, refusal or fault."""


class TradeHistoryConflictError(TradeHistoryError):
    """The write was refused: it would contradict a row already written."""


class TradeHistoryUnavailableError(TradeHistoryError):
    """The database could not be reached, so nothing is claimed either way."""
