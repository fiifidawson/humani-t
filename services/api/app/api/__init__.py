"""The HTTP boundary.

This is the only layer that knows about Pydantic, status codes and JSON. Domain
objects are plain dataclasses; the schemas here translate between untrusted
request bodies and those dataclasses, and back again.

Keeping the translation explicit is what lets the same domain serve a different
transport in V2 without the wire format leaking inwards.
"""
