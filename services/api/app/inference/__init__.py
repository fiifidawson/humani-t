"""Language models: the InferenceProvider seam, its implementations and the prompt guard.

Every model call in the system goes through an `InferenceProvider`, and the
default is a model server the operator runs (Ollama). Model servers' wire
formats stay inside this package.
"""
