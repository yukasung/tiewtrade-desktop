from collections import Counter

from PySide6.QtWidgets import QWidget


def ink_colours(widget: QWidget) -> set[str]:
    """The colours a widget actually paints, read back off its own pixels.

    A colour can be set and still not reach the screen — a palette loses to a
    stylesheet, and a stylesheet rule loses to a more specific one — so a test
    that only reads back what it set would pass while the widget renders in
    the theme's plain colour. Rendering is the only assertion that cannot.
    """
    image = widget.grab().toImage()
    counts: Counter[str] = Counter()
    for y in range(image.height()):
        for x in range(image.width()):
            counts[image.pixelColor(x, y).name()] += 1
    # Anti-aliasing smears the ink across neighbouring shades, so only colours
    # covering a real share of the widget count as painted.
    threshold = max(1, image.width() * image.height() // 200)
    return {colour for colour, count in counts.items() if count >= threshold}
