"""Task registry with manifest support."""

import importlib.util
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from ..models.manifest import ManifestConfig


class TaskRegistry:
    """
    Manages task definitions from manifest directories.

    Features:
    - Loads task configurations from manifest directories
    - Renders Jinja2 templates with parameter validation
    - Caches templates for performance
    """

    def __init__(self, manifests_dir: str):
        self.manifests_dir = Path(manifests_dir)

        # Jinja2 environment rooted at manifests dir
        self.env = Environment(
            loader=FileSystemLoader(self.manifests_dir),
            undefined=StrictUndefined,
            autoescape=False,
        )

        # Load task configurations
        self.manifests: dict[str, ManifestConfig] = {}
        self._load_manifests()

    def _load_manifests(self):
        """Scan manifests directory and load all manifest.json files."""
        for task_dir in self.manifests_dir.iterdir():
            if not task_dir.is_dir():
                continue

            manifest_json = task_dir / "manifest.json"
            if not manifest_json.exists():
                continue

            with open(manifest_json, encoding="utf-8") as f:
                manifest_data = json.load(f)

            config = ManifestConfig(**manifest_data)
            self.manifests[config.task_id] = config

    def get_manifest(self, task_id: str) -> ManifestConfig:
        """Get task manifest configuration by ID."""
        if task_id not in self.manifests:
            raise ValueError(f"Task '{task_id}' not found in registry")
        return self.manifests[task_id]

    def render_prompts(self, task_id: str, **params) -> list[str]:
        """
        Render all prompt templates for a task.

        Merges defaults from parameter definitions, then overrides with
        provided params. Validates that all required parameters are present.
        Returns a list of rendered strings, one per prompt in the manifest.
        """
        manifest = self.get_manifest(task_id)

        if not manifest.prompts:
            raise ValueError(f"Task '{task_id}' has no prompts defined")

        # Build merged params: task-level defaults first, then caller overrides
        merged_params: dict[str, str] = {name: p.default for name, p in manifest.parameters.items()}
        merged_params.update(params)

        # Validate required params
        missing = [
            name
            for name, p in manifest.parameters.items()
            if p.required and not merged_params.get(name)
        ]
        if missing:
            raise ValueError(f"Missing required params for '{task_id}': {missing}")

        # Render all templates
        results = []
        for prompt_config in manifest.prompts:
            template_path = f"{task_id}/{prompt_config.template}"
            template = self.env.get_template(template_path)
            results.append(template.render(**merged_params))
        return results

    def load_output_type(self, task_id: str) -> type[BaseModel] | None:
        """Dynamically import ReturnType from a task's schema.py, if configured."""
        manifest = self.get_manifest(task_id)
        if not manifest.output_schema:
            return None
        schema_path = self.manifests_dir / task_id / manifest.output_schema
        spec = importlib.util.spec_from_file_location(f"_schemas.{task_id}", schema_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ReturnType

    def list_tasks(self) -> list[str]:
        """List all available task IDs."""
        return list(self.manifests.keys())
