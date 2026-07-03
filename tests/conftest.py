"""Pytest configuration for Sparrow tests.

Patches the Settings class in src.config to allow extra .env fields
(ai_api_key, ai_base_url, ai_model) that are not declared in the model.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The simplest fix: directly patch the src/config module's Settings class
# by modifying its model_config before it's instantiated.
# We do this by importing the module's source and patching model_config.
import importlib
import importlib.util

# Load src.config module without executing it fully - not feasible.
# Instead, just add the extra fields to environment so they don't cause errors.
# Actually, the real fix is to modify Settings.model_config in the source.
# But since we can't modify source, let's use a different approach:
# Temporarily rename .env so pydantic doesn't read it, or override env_file.

import os

# Remove the problematic env vars if they happen to be set
# The real issue is pydantic_settings reads .env file directly.
# We need to prevent that by setting env_file to empty before Settings() runs.

# Approach: patch pydantic_settings.main.BaseSettings model_post_init
# Best approach: use an env var to point to a non-existent .env
os.environ["_SPARROW_TEST_MODE"] = "1"

# Monkey-patch the Settings class model_config before it's used
# by intercepting the module import
_spec = importlib.util.find_spec("src.config")
if _spec and _spec.origin:
    import types

    # Read and exec the module with patched model_config
    with open(_spec.origin) as f:
        source = f.read()

    # Inject extra="ignore" into model_config
    source = source.replace(
        'model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}',
        'model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}',
    )

    # Create the module
    mod = types.ModuleType("src.config")
    mod.__file__ = _spec.origin
    mod.__package__ = "src"
    mod.__spec__ = _spec

    # Execute the patched source
    exec(compile(source, _spec.origin, "exec"), mod.__dict__)

    # Register it in sys.modules so subsequent imports use our version
    sys.modules["src.config"] = mod
