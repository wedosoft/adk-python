# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for utilities in cli_create."""


from __future__ import annotations

import click
import os
import pytest
import subprocess

import google.adk.cli.cli_create as cli_create

from pathlib import Path
from typing import Any, Dict, List, Tuple

# Helpers
class _Recorder:
    """A callable object that records every invocation."""

    def __init__(self) -> None:
        self.calls: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        self.calls.append((args, kwargs))


# Fixtures
@pytest.fixture(autouse=True)
def _mute_click(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence click output in every test."""
    monkeypatch.setattr(click, "echo", lambda *a, **k: None)
    monkeypatch.setattr(click, "secho", lambda *a, **k: None)


@pytest.fixture()
def agent_folder(tmp_path: Path) -> Path:
    """Return a temporary path that will hold generated agent sources."""
    return tmp_path / "agent"


# _generate_files
def test_generate_files_with_api_key(agent_folder: Path) -> None:
    """Files should be created with the API-key backend and correct .env flags."""
    cli_create._generate_files(
        str(agent_folder),
        google_api_key="dummy-key",
        model="gemini-2.0-flash-001",
    )

    env_content = (agent_folder / ".env").read_text()
    assert "GOOGLE_API_KEY=dummy-key" in env_content
    assert "GOOGLE_GENAI_USE_VERTEXAI=0" in env_content
    assert (agent_folder / "agent.py").exists()
    assert (agent_folder / "__init__.py").exists()


def test_generate_files_with_gcp(agent_folder: Path) -> None:
    """Files should be created with Vertex AI backend and correct .env flags."""
    cli_create._generate_files(
        str(agent_folder),
        google_cloud_project="proj",
        google_cloud_region="us-central1",
        model="gemini-2.0-flash-001",
    )

    env_content = (agent_folder / ".env").read_text()
    assert "GOOGLE_CLOUD_PROJECT=proj" in env_content
    assert "GOOGLE_CLOUD_LOCATION=us-central1" in env_content
    assert "GOOGLE_GENAI_USE_VERTEXAI=1" in env_content


def test_generate_files_overwrite(agent_folder: Path) -> None:
    """Existing files should be overwritten when generating again."""
    agent_folder.mkdir(parents=True, exist_ok=True)
    (agent_folder / ".env").write_text("OLD")

    cli_create._generate_files(
        str(agent_folder),
        google_api_key="new-key",
        model="gemini-2.0-flash-001",
    )

    assert "GOOGLE_API_KEY=new-key" in (agent_folder / ".env").read_text()


def test_generate_files_permission_error(monkeypatch: pytest.MonkeyPatch, agent_folder: Path) -> None:
    """PermissionError raised by os.makedirs should propagate."""
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        cli_create._generate_files(str(agent_folder), model="gemini-2.0-flash-001")


def test_generate_files_no_params(agent_folder: Path) -> None:
    """No backend parameters → minimal .env file is generated."""
    cli_create._generate_files(str(agent_folder), model="gemini-2.0-flash-001")

    env_content = (agent_folder / ".env").read_text()
    for key in ("GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI"):
        assert key not in env_content


# run_cmd
def test_run_cmd_overwrite_reject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """User rejecting overwrite should trigger click.Abort."""
    agent_name = "agent"
    agent_dir = tmp_path / agent_name
    agent_dir.mkdir()
    (agent_dir / "dummy.txt").write_text("dummy")

    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(os.path, "exists", lambda _p: True)
    monkeypatch.setattr(os, "listdir", lambda _p: ["dummy.txt"])
    monkeypatch.setattr(click, "confirm", lambda *a, **k: False)

    with pytest.raises(click.Abort):
        cli_create.run_cmd(
            agent_name,
            model="gemini-2.0-flash-001",
            google_api_key=None,
            google_cloud_project=None,
            google_cloud_region=None,
        )


# Prompt helpers
def test_prompt_for_google_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt should return the project input."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "test-proj")
    assert cli_create._prompt_for_google_cloud(None) == "test-proj"


def test_prompt_for_google_cloud_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt should return the region input."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "asia-northeast1")
    assert cli_create._prompt_for_google_cloud_region(None) == "asia-northeast1"


def test_prompt_for_google_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt should return the API-key input."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "api-key")
    assert cli_create._prompt_for_google_api_key(None) == "api-key"


def test_prompt_for_model_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting option '1' should return the default Gemini model string."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "1")
    assert cli_create._prompt_for_model() == "gemini-2.0-flash-001"


def test_prompt_for_model_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting option '2' should return placeholder and call secho."""
    called: Dict[str, bool] = {}

    monkeypatch.setattr(click, "prompt", lambda *a, **k: "2")

    def _fake_secho(*_a: Any, **_k: Any) -> None:
        called["secho"] = True

    monkeypatch.setattr(click, "secho", _fake_secho)
    assert cli_create._prompt_for_model() == "<FILL_IN_MODEL>"
    assert called.get("secho") is True



# Backend selection helper
def test_prompt_to_choose_backend_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing API-key backend returns (api_key, None, None)."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "1")
    monkeypatch.setattr(cli_create, "_prompt_for_google_api_key", lambda _v: "api-key")

    api_key, proj, region = cli_create._prompt_to_choose_backend(None, None, None)
    assert api_key == "api-key"
    assert proj is None and region is None


def test_prompt_to_choose_backend_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing Vertex backend returns (None, project, region)."""
    monkeypatch.setattr(click, "prompt", lambda *a, **k: "2")
    monkeypatch.setattr(cli_create, "_prompt_for_google_cloud", lambda _v: "proj")
    monkeypatch.setattr(cli_create, "_prompt_for_google_cloud_region", lambda _v: "region")

    api_key, proj, region = cli_create._prompt_to_choose_backend(None, None, None)
    assert api_key is None
    assert proj == "proj"
    assert region == "region"



# prompt_str
def test_prompt_str_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_prompt_str should retry until a non-blank string is provided."""
    responses = iter(["", " ", "valid"])
    monkeypatch.setattr(click, "prompt", lambda *_a, **_k: next(responses))
    assert cli_create._prompt_str("dummy") == "valid"



# gcloud fallback helpers
def test_get_gcp_project_from_gcloud_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure of gcloud project lookup should return empty string."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert cli_create._get_gcp_project_from_gcloud() == ""


def test_get_gcp_region_from_gcloud_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """CalledProcessError should result in empty region string."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "gcloud")),
    )
    assert cli_create._get_gcp_region_from_gcloud() == ""
