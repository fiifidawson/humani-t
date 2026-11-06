"""The HTTP boundary.

This is the only layer that knows about Pydantic, status codes and JSON. Domain
objects are plain dataclasses; the schemas here translate between untrusted
request bodies and those dataclasses, and back again.

Keeping the translation explicit is what stops the wire format leaking inwards:
a change to a JSON field is a change to this package, never to the domain.
"""
