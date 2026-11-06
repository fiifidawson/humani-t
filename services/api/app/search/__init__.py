"""Paper search: the PaperSearchProvider seam and its implementations.

One interface over OpenAlex, Europe PMC and arXiv. Each has its own query
syntax, rate limits and metadata shape, and none of that is allowed above this
package. Google Scholar is deliberately absent: it has no API, and its terms
forbid automated queries.
"""
