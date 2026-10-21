from key_value import parse_pair

assert parse_pair("name=kestra") == ("name", "kestra")
assert parse_pair("query=a=b=c") == ("query", "a=b=c")
