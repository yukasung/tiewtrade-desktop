"""The mark a screen shows where there is no value to show.

One character, named once, so a missing figure reads the same wherever it
appears. It is a dash rather than an empty cell because a blank cell reads
as a column that failed to render, while a dash says the question was
asked and has no answer yet.
"""

BLANK = "—"
