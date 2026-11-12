"""The HTTP boundary: the only layer that knows about Pydantic, status codes and JSON.

Schemas here translate request bodies into domain dataclasses and back, so a change
to a JSON field stays in this package and never reaches the domain.
"""
