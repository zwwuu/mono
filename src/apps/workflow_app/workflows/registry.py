from typing import Dict, List, Optional

from apps.workflow_app.workflows.base import WorkflowDefinition


class WorkflowRegistry:
    """Central registry holding all active workflow definitions."""
    _registry: Dict[str, WorkflowDefinition] = {}

    @classmethod
    def register(cls, definition: WorkflowDefinition):
        cls._registry[definition.type_id] = definition

    @classmethod
    def get(cls, type_id: str) -> Optional[WorkflowDefinition]:
        return cls._registry.get(type_id)

    @classmethod
    def list_all(cls) -> List[WorkflowDefinition]:
        return list(cls._registry.values())

    @classmethod
    def clear(cls):
        """Used mainly for test isolation if needed."""
        cls._registry.clear()
