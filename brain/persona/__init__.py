"""莲心人格枢控的领域层公共接口。"""

from brain.persona.composer import CompiledPrompt, PersonaPromptComposer, PromptLayer
from brain.persona.defaults import build_default_persona, build_naga_style_persona
from brain.persona.manager import PersonaManager, get_persona_manager
from brain.persona.models import (
    DEFAULT_PERSONA_ID,
    PERSONA_SCHEMA_VERSION,
    PersonaProfile,
    PersonaSnapshot,
    PersonaValidationError,
)
from brain.persona.runtime import (
    active_assistant_name,
    capture_persona_snapshot,
    compose_scene_prompt,
)
from brain.persona.store import PersonaNotFoundError, PersonaStore, PersonaStoreError

__all__ = [
    "CompiledPrompt",
    "DEFAULT_PERSONA_ID",
    "PERSONA_SCHEMA_VERSION",
    "PersonaManager",
    "PersonaNotFoundError",
    "PersonaProfile",
    "PersonaPromptComposer",
    "PersonaSnapshot",
    "PersonaStore",
    "PersonaStoreError",
    "PersonaValidationError",
    "PromptLayer",
    "build_default_persona",
    "build_naga_style_persona",
    "active_assistant_name",
    "capture_persona_snapshot",
    "compose_scene_prompt",
    "get_persona_manager",
]
