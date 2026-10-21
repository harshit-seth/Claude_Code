from safe_math import safe_divide

assert safe_divide(8, 2) == 4
assert safe_divide(8, 0) is None
