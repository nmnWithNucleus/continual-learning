"""Pinned-environment execution.

Morpheus spans two environments that cannot be merged: training/eval needs
torch+peft+transformers, judging needs litellm with Vertex ADC. They are invoked
by ABSOLUTE INTERPRETER PATH — never `conda activate`.

That is not a style preference. The research chain's `phased_run.sh` did
`conda activate speedlora && python phase_d_driver.py` and died a minute in
because activate did not reorder PATH inside a non-interactive shell: the
`python` that ran was a different one, without peft. A nightly job that fails
that way fails AFTER the corpus is built and burns the window.

So: resolve the interpreter, PREFLIGHT its imports, then run. The preflight costs
under a second and turns a 40-minute failure into an immediate one.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


class EnvironmentUnusable(RuntimeError):
    """The pinned interpreter is missing, or missing what it needs to run."""


@dataclass(frozen=True)
class PinnedEnv:
    name: str
    interpreter: str
    requires: tuple[str, ...] = ()        # modules preflighted before any real work
    env: Mapping[str, str] = field(default_factory=dict)

    def resolved(self) -> Path:
        path = Path(self.interpreter)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise EnvironmentUnusable(
                f"{self.name}: no executable interpreter at {self.interpreter!r}. "
                "Morpheus never activates environments — set the absolute path "
                "(MORPHEUS_TRAIN_PYTHON / MORPHEUS_JUDGE_PYTHON) or point at a container.")
        return path

    def preflight(self) -> None:
        """Fail now, loudly, rather than after the GPU work is already spent."""
        interpreter = self.resolved()
        if not self.requires:
            return
        probe = "import " + ", ".join(self.requires)
        result = subprocess.run([str(interpreter), "-c", probe],
                                capture_output=True, text=True, env=self._environ())
        if result.returncode != 0:
            raise EnvironmentUnusable(
                f"{self.name} ({interpreter}) cannot import {', '.join(self.requires)}:\n"
                f"{result.stderr.strip()}")

    def run(self, argv: Sequence[str], *, cwd: str | Path | None = None,
            check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
        interpreter = self.resolved()
        return subprocess.run([str(interpreter), *argv], cwd=cwd, check=check,
                              env=self._environ(), text=True,
                              capture_output=capture)

    def freeze(self) -> str:
        """The env's installed package set — captured as a run's lockfile."""
        result = self.run(["-m", "pip", "freeze"], check=False, capture=True)
        if result.returncode != 0:
            raise EnvironmentUnusable(f"{self.name}: pip freeze failed:\n{result.stderr}")
        return result.stdout

    def _environ(self) -> dict[str, str]:
        return {**os.environ, **self.env}


def train_env(settings) -> PinnedEnv:
    """CUDA visibility is set by DEVICE INDEX here rather than assumed: the node's
    GPUs are shared, and hardcoding GPU 0 is how nightly jobs collide."""
    index = settings.device.rsplit(":", 1)[-1] if ":" in settings.device else "0"
    return PinnedEnv(name="train", interpreter=settings.train_python,
                     requires=("torch", "transformers", "peft"),
                     env={"CUDA_VISIBLE_DEVICES": index,
                          "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})


def judge_env(settings) -> PinnedEnv:
    return PinnedEnv(name="judge", interpreter=settings.judge_python,
                     requires=("litellm",),
                     env={"VERTEX_PROJECT": settings.vertex_project})
