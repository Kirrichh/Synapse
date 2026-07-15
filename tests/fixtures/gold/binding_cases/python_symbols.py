"""Static committed source for Stage 4 binding acceptance."""


def fixture_decorator(function):
    return function


@fixture_decorator
def top_level(value):
    """Return a deterministic value."""
    return value + 1


async def async_top_level(value):
    return value


class Outer:
    class Inner:
        def method(self, value):
            return value * 2

        async def async_method(self, value):
            return value
