"""Find and load template renderers.

A template can be rendered once its directory has a ``renderer.py`` defining
``render(ctx: RenderContext) -> RenderedArtifact``. Its optional ``phrases.yaml``
is the phrase bank. Both are covered by the template content hash.
"""

import hashlib
import importlib.util
from types import ModuleType
from typing import Any, Callable, Dict, Optional

import yaml

from ..ids import rng_for
from ..scenarios import ArtifactPlan, ScenarioPlan
from ..templates import Template, TemplateRegistry
from .base import Case, RenderContext, RenderedArtifact
from .stories import Story, product_return_damage

STORIES: Dict[str, Callable[[RenderContext], Story]] = {
    "product_return_damage": product_return_damage,
}

Renderer = Callable[[RenderContext], RenderedArtifact]

_modules: Dict[str, ModuleType] = {}


def _load_module(template: Template) -> ModuleType:
    key = template.content_hash
    if key not in _modules:
        path = template.path / "renderer.py"
        suffix = hashlib.sha256(key.encode()).hexdigest()[:12]
        name = f"helios_ds_template_{template.template_id}_{suffix}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[key] = module
    return _modules[key]


def has_renderer(template: Template) -> bool:
    return (template.path / "renderer.py").is_file()


def renderer_for(template: Template) -> Optional[Renderer]:
    if not has_renderer(template):
        return None
    render: Renderer = _load_module(template).render
    return render


def _phrases(template: Template) -> Dict[str, Any]:
    path = template.path / "phrases.yaml"
    loaded: Dict[str, Any] = yaml.safe_load(path.read_text()) if path.is_file() else {}
    return loaded


def story_for(scenario: ScenarioPlan) -> Optional[Story]:
    """The scenario's shared story (canonical names, claims, relationships), if its
    scenario type has renderers yet."""
    build = STORIES.get(scenario.scenario_type)
    if build is None or not scenario.artifacts:
        return None
    ctx = RenderContext(
        scenario=scenario,
        artifact=scenario.artifacts[0],
        phrases={},
        rendering_parameters={},
        case=Case.for_scenario(scenario),
        rng=rng_for(scenario.scenario_seed),
    )
    return build(ctx)


def render_artifact(
    templates: TemplateRegistry, scenario: ScenarioPlan, artifact: ArtifactPlan
) -> Optional[RenderedArtifact]:
    """Render one planned artifact, or None if its template has no renderer yet."""
    template = templates.get(artifact.template_id)
    if template.template_version != artifact.template_version:
        raise ValueError(
            f"{artifact.artifact_id}: planned with {artifact.template_id} "
            f"v{artifact.template_version}, registry has v{template.template_version}"
        )
    render = renderer_for(template)
    if render is None:
        return None
    ctx = RenderContext(
        scenario=scenario,
        artifact=artifact,
        phrases=_phrases(template),
        rendering_parameters=dict(template.spec.rendering_parameters),
        case=Case.for_scenario(scenario),
        rng=rng_for(artifact.artifact_seed),
    )
    return render(ctx)
