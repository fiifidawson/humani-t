"""Domain entities and rules.

Nothing in this package may import a database, an HTTP client, an LLM SDK, or
FastAPI. These types are plain Python and must stay that way — they are the only
part of the system guaranteed to survive the move from a server to a laptop.

Note the absence of Pydantic. Pydantic models belong at the API boundary, where
untrusted JSON arrives. Down here, a Concept is already valid or it was never
constructed.
"""
