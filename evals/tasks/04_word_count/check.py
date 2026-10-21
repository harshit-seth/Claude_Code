from words import count_words

assert count_words("one two  three") == 3
assert count_words(" \t\n") == 0
