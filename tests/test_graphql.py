from intelliw.graphql import schema


def test_hello():
    result = schema.execute_sync("{ hello { message } }")
    assert result.errors is None
    assert result.data == {"hello": {"message": "hello world"}}
