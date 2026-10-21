Fix `parse_pair` in `key_value.py` so it splits on only the first equals sign.
Return a `(key, value)` tuple, preserving any later equals signs in the value.
