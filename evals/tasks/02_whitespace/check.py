from text_utils import normalize_whitespace

assert normalize_whitespace("  hello\tworld\nagain  ") == "hello world again"
assert normalize_whitespace("") == ""
