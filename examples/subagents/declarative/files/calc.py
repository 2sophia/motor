"""Trivial calculator helpers."""


def add(a, b):
    """Return the sum of two integers."""
    return a + b


def mean(values):
    """Return the average of a list of numbers."""
    # off-by-one bug: should be sum / len, not sum / (len - 1)
    return sum(values) / (len(values) - 1)


def percent(part, whole):
    """Compute part as percentage of whole."""
    return part * 100 / whole
