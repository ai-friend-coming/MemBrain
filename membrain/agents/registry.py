"""Task registry with manifest support and profile-aware fallback."""

import importlib.util
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from pydantic import BaseModel

from membrain.agents.manifest import ManifestConfig


class TaskRegistry:
    """
    Manages task definitions from manifest directories.

    Profile resolution order for any template file:
      1. manifests/{profile}/{task_id}/{file}  (profile override)
      2. manifests/{task_id}/{file}            (generic default)

    Profile resolution for manifest.json:
      1. manifests/{profile}/{task_id}/manifest.json  (profile has its own config)
      2. manifests/{task_id}/manifest.json            (fallback to default config)
    """

    def __init__(self, manifests_dir: str):
        self.manifests_dir = Path(manifests_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.manifests_dir)),
            undefined=StrictUndefined,
            autoescape=False,
        )
        self.manifests: dict[str, ManifestConfig] = {}
        self._output_types: dict[str, type[BaseModel]] = {}
        self._load_manifests()

    def _load_manifests(self):
        """Scan top-level manifests directory and load all manifest.json files."""
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

    def _load_profile_manifest(
        self, task_id: str, profile: str
    ) -> ManifestConfig | None:
        """Load and cache a profile-specific manifest.json (lazy, idempotent)."""
        cache_key = f"{profile}/{task_id}"
        if cache_key in self.manifests:
            return self.manifests[cache_key]
        manifest_path = self.manifests_dir / profile / task_id / "manifest.json"
        if not manifest_path.exists():
            return None
        with open(manifest_path, encoding="utf-8") as f:
            config = ManifestConfig(**json.load(f))
        self.manifests[cache_key] = config
        return config

    def get_manifest(self, task_id: str, profile: str | None = None) -> ManifestConfig:
        """Get task manifest, checking profile directory first."""
        if profile:
            profile_manifest = self._load_profile_manifest(task_id, profile)
            if profile_manifest is not None:
                return profile_manifest
        if task_id not in self.manifests:
            raise ValueError(f"Task '{task_id}' not found in registry")
        return self.manifests[task_id]

    def _resolve_template(
        self, task_id: str, filename: str, profile: str | None
    ) -> str:
        """Return the Jinja2 template path to use, with profile fallback."""
        if profile:
            candidate = f"{profile}/{task_id}/{filename}"
            try:
                self.env.get_template(candidate)
                return candidate
            except TemplateNotFound:
                pass
        return f"{task_id}/{filename}"

    def render_prompts(
        self, task_id: str, profile: str | None = None, **params: str
    ) -> list[str]:
        """Render all prompt templates for a task with merged parameters."""
        manifest = self.get_manifest(task_id, profile)
        if not manifest.prompts:
            raise ValueError(f"Task '{task_id}' has no prompts defined")

        merged: dict[str, str] = {
            name: p.default for name, p in manifest.parameters.items()
        }
        merged.update(params)

        missing = [
            name
            for name, p in manifest.parameters.items()
            if p.required and name not in merged
        ]
        if missing:
            raise ValueError(f"Missing required params for '{task_id}': {missing}")

        results = []
        for prompt_config in manifest.prompts:
            template_path = self._resolve_template(
                task_id, prompt_config.template, profile
            )
            template = self.env.get_template(template_path)
            results.append(template.render(**merged))
        return results

    def load_output_type(
        self, task_id: str, profile: str | None = None
    ) -> type[BaseModel] | None:
        """Dynamically import ReturnType from a task's schema.py (cached)."""
        cache_key = f"{profile}/{task_id}" if profile else task_id
        if cache_key in self._output_types:
            return self._output_types[cache_key]
        manifest = self.get_manifest(task_id, profile)
        if not manifest.output_schema:
            return None
        # Check profile dir first, then default
        if profile:
            profile_path = (
                self.manifests_dir / profile / task_id / manifest.output_schema
            )
            schema_path = (
                profile_path
                if profile_path.exists()
                else self.manifests_dir / task_id / manifest.output_schema
            )
        else:
            schema_path = self.manifests_dir / task_id / manifest.output_schema
        spec = importlib.util.spec_from_file_location(cache_key, schema_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self._output_types[cache_key] = module.ReturnType
        return module.ReturnType
