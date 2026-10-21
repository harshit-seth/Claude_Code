from limits import clamp

assert clamp(-3, 0, 10) == 0
assert clamp(7, 0, 10) == 7
assert clamp(14, 0, 10) == 10
