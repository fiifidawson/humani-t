"""Domain entities and rules.

Nothing in this package may import a database, an HTTP client, an LLM SDK, or
FastAPI. These types are plain Python and must stay that way — it is what lets
them be tested without a database or a network, and swapped providers leave
them untouched.

Note the absence of Pydantic. Pydantic models belong at the API boundary, where
untrusted JSON arrives. Down here, a Concept is already valid or it was never
constructed.
"""
