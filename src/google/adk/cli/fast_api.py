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

import asyncio
from contextlib import asynccontextmanager
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import traceback
import typing
from typing import Any
from typing import List
from typing import Literal
from typing import Optional

import click
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket
from fastapi.websockets import WebSocketDisconnect
from google.genai import types
import graphviz
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import export
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import alias_generators
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import ValidationError
from starlette.types import Lifespan

from ..agents import RunConfig
from ..agents.base_agent import BaseAgent
from ..agents.live_request_queue import LiveRequest
from ..agents.live_request_queue import LiveRequestQueue
from ..agents.llm_agent import Agent
from ..agents.llm_agent import LlmAgent
from ..agents.run_config import StreamingMode
from ..artifacts import InMemoryArtifactService
from ..events.event import Event
from ..memory.in_memory_memory_service import InMemoryMemoryService
from ..runners import Runner
from ..sessions.database_session_service import DatabaseSessionService
from ..sessions.in_memory_session_service import InMemorySessionService
from ..sessions.session import Session
from ..sessions.vertex_ai_session_service import VertexAiSessionService
from ..tools.base_toolset import BaseToolset
from .cli_eval import EVAL_SESSION_ID_PREFIX
from .cli_eval import EvalCaseResult
from .cli_eval import EvalMetric
from .cli_eval import EvalMetricResult
from .cli_eval import EvalSetResult
from .cli_eval import EvalStatus
from .utils import common
from .utils import create_empty_state
from .utils import envs
from .utils import evals

logger = logging.getLogger(__name__)

_EVAL_SET_FILE_EXTENSION = ".evalset.json"
_EVAL_SET_RESULT_FILE_EXTENSION = ".evalset_result.json"


class ApiServerSpanExporter(export.SpanExporter):

  def __init__(self, trace_dict):
    self.trace_dict = trace_dict

  def export(
      self, spans: typing.Sequence[ReadableSpan]
  ) -> export.SpanExportResult:
    for span in spans:
      if (
          span.name == "call_llm"
          or span.name == "send_data"
          or span.name.startswith("tool_response")
      ):
        attributes = dict(span.attributes)
        attributes["trace_id"] = span.get_span_context().trace_id
        attributes["span_id"] = span.get_span_context().span_id
        if attributes.get("gcp.vertex.agent.event_id", None):
          self.trace_dict[attributes["gcp.vertex.agent.event_id"]] = attributes
    return export.SpanExportResult.SUCCESS

  def force_flush(self, timeout_millis: int = 30000) -> bool:
    return True


class InMemoryExporter(export.SpanExporter):

  def __init__(self, trace_dict):
    super().__init__()
    self._spans = []
    self.trace_dict = trace_dict

  def export(
      self, spans: typing.Sequence[ReadableSpan]
  ) -> export.SpanExportResult:
    for span in spans:
      trace_id = span.context.trace_id
      if span.name == "call_llm":
        attributes = dict(span.attributes)
        session_id = attributes.get("gcp.vertex.agent.session_id", None)
        if session_id:
          if session_id not in self.trace_dict:
            self.trace_dict[session_id] = [trace_id]
          else:
            self.trace_dict[session_id] += [trace_id]
    self._spans.extend(spans)
    return export.SpanExportResult.SUCCESS

  def get_finished_spans(self, session_id: str):
    trace_ids = self.trace_dict.get(session_id, None)
    if trace_ids is None or not trace_ids:
      return []
    return [x for x in self._spans if x.context.trace_id in trace_ids]

  def force_flush(self, timeout_millis: int = 30000) -> bool:
    return True

  def clear(self):
    self._spans.clear()


class AgentRunRequest(BaseModel):
  app_name: str
  user_id: str
  session_id: str
  new_message: types.Content
  streaming: bool = False


class AddSessionToEvalSetRequest(common.BaseModel):
  eval_id: str
  session_id: str
  user_id: str


class RunEvalRequest(common.BaseModel):
  eval_ids: list[str]  # if empty, then all evals in the eval set are run.
  eval_metrics: list[EvalMetric]


class RunEvalResult(common.BaseModel):
  eval_set_file: str
  eval_set_id: str
  eval_id: str
  final_eval_status: EvalStatus
  eval_metric_results: list[tuple[EvalMetric, EvalMetricResult]]
  user_id: str
  session_id: str


def get_fast_api_app(
    *,
    agent_dir: str,
    session_db_url: str = "",
    allow_origins: Optional[list[str]] = None,
    web: bool,
    trace_to_cloud: bool = False,
    lifespan: Optional[Lifespan[FastAPI]] = None,
) -> FastAPI:
  # InMemory tracing dict.
  trace_dict: dict[str, Any] = {}
  session_trace_dict: dict[str, Any] = {}

  # Set up tracing in the FastAPI server.
  provider = TracerProvider()
  provider.add_span_processor(
      export.SimpleSpanProcessor(ApiServerSpanExporter(trace_dict))
  )
  memory_exporter = InMemoryExporter(session_trace_dict)
  provider.add_span_processor(export.SimpleSpanProcessor(memory_exporter))
  if trace_to_cloud:
    envs.load_dotenv_for_agent("", agent_dir)
    if project_id := os.environ.get("GOOGLE_CLOUD_PROJECT", None):
      processor = export.BatchSpanProcessor(
          CloudTraceSpanExporter(project_id=project_id)
      )
      provider.add_span_processor(processor)
    else:
      logging.warning(
          "GOOGLE_CLOUD_PROJECT environment variable is not set. Tracing will"
          " not be enabled."
      )

  trace.set_tracer_provider(provider)

  exit_stacks = []
  toolsets_to_close: set[BaseToolset] = set()

  @asynccontextmanager
  async def internal_lifespan(app: FastAPI):
    if lifespan:
      async with lifespan(app) as lifespan_context:
        yield

        if exit_stacks:
          for stack in exit_stacks:
            await stack.aclose()
        for toolset in toolsets_to_close:
          await toolset.close()
    else:
      yield

  # Run the FastAPI server.
  app = FastAPI(lifespan=internal_lifespan)

  if allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

  if agent_dir not in sys.path:
    sys.path.append(agent_dir)

  runner_dict = {}
  root_agent_dict = {}

  # Build the Artifact service
  artifact_service = InMemoryArtifactService()
  memory_service = InMemoryMemoryService()

  # Build the Session service
  agent_engine_id = ""
  if session_db_url:
    if session_db_url.startswith("agentengine://"):
      # Create vertex session service
      agent_engine_id = session_db_url.split("://")[1]
      if not agent_engine_id:
        raise click.ClickException("Agent engine id can not be empty.")
      envs.load_dotenv_for_agent("", agent_dir)
      session_service = VertexAiSessionService(
          os.environ["GOOGLE_CLOUD_PROJECT"],
          os.environ["GOOGLE_CLOUD_LOCATION"],
      )
    else:
      session_service = DatabaseSessionService(db_url=session_db_url)
  else:
    session_service = InMemorySessionService()

  @app.get("/list-apps")
  def list_apps() -> list[str]:
    base_path = Path.cwd() / agent_dir
    if not base_path.exists():
      raise HTTPException(status_code=404, detail="Path not found")
    if not base_path.is_dir():
      raise HTTPException(status_code=400, detail="Not a directory")
    agent_names = [
        x
        for x in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, x))
        and not x.startswith(".")
        and x != "__pycache__"
    ]
    agent_names.sort()
    return agent_names

  @app.get("/debug/trace/{event_id}")
  def get_trace_dict(event_id: str) -> Any:
    event_dict = trace_dict.get(event_id, None)
    if event_dict is None:
      raise HTTPException(status_code=404, detail="Trace not found")
    return event_dict

  @app.get("/debug/trace/session/{session_id}")
  def get_session_trace(session_id: str) -> Any:
    spans = memory_exporter.get_finished_spans(session_id)
    if not spans:
      return []
    return [
        {
            "name": s.name,
            "span_id": s.context.span_id,
            "trace_id": s.context.trace_id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "attributes": dict(s.attributes),
            "parent_span_id": s.parent.span_id if s.parent else None,
        }
        for s in spans
    ]

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}",
      response_model_exclude_none=True,
  )
  def get_session(app_name: str, user_id: str, session_id: str) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    session = session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")
    return session

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions",
      response_model_exclude_none=True,
  )
  def list_sessions(app_name: str, user_id: str) -> list[Session]:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    return [
        session
        for session in session_service.list_sessions(
            app_name=app_name, user_id=user_id
        ).sessions
        # Remove sessions that were generated as a part of Eval.
        if not session.id.startswith(EVAL_SESSION_ID_PREFIX)
    ]

  @app.post(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}",
      response_model_exclude_none=True,
  )
  def create_session_with_id(
      app_name: str,
      user_id: str,
      session_id: str,
      state: Optional[dict[str, Any]] = None,
  ) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    if (
        session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        is not None
    ):
      logger.warning("Session already exists: %s", session_id)
      raise HTTPException(
          status_code=400, detail=f"Session already exists: {session_id}"
      )
    logger.info("New session created: %s", session_id)
    return session_service.create_session(
        app_name=app_name, user_id=user_id, state=state, session_id=session_id
    )

  @app.post(
      "/apps/{app_name}/users/{user_id}/sessions",
      response_model_exclude_none=True,
  )
  def create_session(
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]] = None,
  ) -> Session:
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    logger.info("New session created")
    return session_service.create_session(
        app_name=app_name, user_id=user_id, state=state
    )

  def _get_eval_set_file_path(app_name, agent_dir, eval_set_id) -> str:
    return os.path.join(
        agent_dir,
        app_name,
        eval_set_id + _EVAL_SET_FILE_EXTENSION,
    )

  @app.post(
      "/apps/{app_name}/eval_sets/{eval_set_id}",
      response_model_exclude_none=True,
  )
  def create_eval_set(
      app_name: str,
      eval_set_id: str,
  ):
    """Creates an eval set, given the id."""
    pattern = r"^[a-zA-Z0-9_]+$"
    if not bool(re.fullmatch(pattern, eval_set_id)):
      raise HTTPException(
          status_code=400,
          detail=(
              f"Invalid eval set id. Eval set id should have the `{pattern}`"
              " format"
          ),
      )
    # Define the file path
    new_eval_set_path = _get_eval_set_file_path(
        app_name, agent_dir, eval_set_id
    )

    logger.info("Creating eval set file `%s`", new_eval_set_path)

    if not os.path.exists(new_eval_set_path):
      # Write the JSON string to the file
      logger.info("Eval set file doesn't exist, we will create a new one.")
      with open(new_eval_set_path, "w") as f:
        empty_content = json.dumps([], indent=2)
        f.write(empty_content)

  @app.get(
      "/apps/{app_name}/eval_sets",
      response_model_exclude_none=True,
  )
  def list_eval_sets(app_name: str) -> list[str]:
    """Lists all eval sets for the given app."""
    eval_set_file_path = os.path.join(agent_dir, app_name)
    eval_sets = []
    for file in os.listdir(eval_set_file_path):
      if file.endswith(_EVAL_SET_FILE_EXTENSION):
        eval_sets.append(
            os.path.basename(file).removesuffix(_EVAL_SET_FILE_EXTENSION)
        )

    return sorted(eval_sets)

  @app.post(
      "/apps/{app_name}/eval_sets/{eval_set_id}/add_session",
      response_model_exclude_none=True,
  )
  async def add_session_to_eval_set(
      app_name: str, eval_set_id: str, req: AddSessionToEvalSetRequest
  ):
    pattern = r"^[a-zA-Z0-9_]+$"
    if not bool(re.fullmatch(pattern, req.eval_id)):
      raise HTTPException(
          status_code=400,
          detail=f"Invalid eval id. Eval id should have the `{pattern}` format",
      )

    # Get the session
    session = session_service.get_session(
        app_name=app_name, user_id=req.user_id, session_id=req.session_id
    )
    assert session, "Session not found."
    # Load the eval set file data
    eval_set_file_path = _get_eval_set_file_path(
        app_name, agent_dir, eval_set_id
    )
    with open(eval_set_file_path, "r") as file:
      eval_set_data = json.load(file)  # Load JSON into a list

    if [x for x in eval_set_data if x["name"] == req.eval_id]:
      raise HTTPException(
          status_code=400,
          detail=(
              f"Eval id `{req.eval_id}` already exists in `{eval_set_id}`"
              " eval set."
          ),
      )

    # Convert the session data to evaluation format
    test_data = evals.convert_session_to_eval_format(session)

    # Populate the session with initial session state.
    initial_session_state = create_empty_state(
        await _get_root_agent_async(app_name)
    )

    eval_set_data.append({
        "name": req.eval_id,
        "data": test_data,
        "initial_session": {
            "state": initial_session_state,
            "app_name": app_name,
            "user_id": req.user_id,
        },
    })
    # Serialize the test data to JSON and write to the eval set file.
    with open(eval_set_file_path, "w") as f:
      f.write(json.dumps(eval_set_data, indent=2))

  @app.get(
      "/apps/{app_name}/eval_sets/{eval_set_id}/evals",
      response_model_exclude_none=True,
  )
  def list_evals_in_eval_set(
      app_name: str,
      eval_set_id: str,
  ) -> list[str]:
    """Lists all evals in an eval set."""
    # Load the eval set file data
    eval_set_file_path = _get_eval_set_file_path(
        app_name, agent_dir, eval_set_id
    )
    with open(eval_set_file_path, "r") as file:
      eval_set_data = json.load(file)  # Load JSON into a list

    return sorted([x["name"] for x in eval_set_data])

  @app.post(
      "/apps/{app_name}/eval_sets/{eval_set_id}/run_eval",
      response_model_exclude_none=True,
  )
  async def run_eval(
      app_name: str, eval_set_id: str, req: RunEvalRequest
  ) -> list[RunEvalResult]:
    from .cli_eval import run_evals

    """Runs an eval given the details in the eval request."""
    # Create a mapping from eval set file to all the evals that needed to be
    # run.
    envs.load_dotenv_for_agent(os.path.basename(app_name), agent_dir)
    eval_set_file_path = _get_eval_set_file_path(
        app_name, agent_dir, eval_set_id
    )
    eval_set_to_evals = {eval_set_file_path: req.eval_ids}

    if not req.eval_ids:
      logger.info(
          "Eval ids to run list is empty. We will all evals in the eval set."
      )
    root_agent = await _get_root_agent_async(app_name)
    run_eval_results = []
    eval_case_results = []
    async for eval_result in run_evals(
        eval_set_to_evals,
        root_agent,
        getattr(root_agent, "reset_data", None),
        req.eval_metrics,
        session_service=session_service,
        artifact_service=artifact_service,
    ):
      run_eval_results.append(
          RunEvalResult(
              app_name=app_name,
              eval_set_file=eval_result.eval_set_file,
              eval_set_id=eval_set_id,
              eval_id=eval_result.eval_id,
              final_eval_status=eval_result.final_eval_status,
              eval_metric_results=eval_result.eval_metric_results,
              user_id=eval_result.user_id,
              session_id=eval_result.session_id,
          )
      )
      session = session_service.get_session(
          app_name=app_name,
          user_id=eval_result.user_id,
          session_id=eval_result.session_id,
      )
      eval_case_results.append(
          EvalCaseResult(
              eval_set_file=eval_result.eval_set_file,
              eval_id=eval_result.eval_id,
              final_eval_status=eval_result.final_eval_status,
              eval_metric_results=eval_result.eval_metric_results,
              session_id=eval_result.session_id,
              session_details=session,
              user_id=eval_result.user_id,
          )
      )

    timestamp = time.time()
    eval_set_result_name = app_name + "_" + eval_set_id + "_" + str(timestamp)
    eval_set_result = EvalSetResult(
        eval_set_result_id=eval_set_result_name,
        eval_set_result_name=eval_set_result_name,
        eval_set_id=eval_set_id,
        eval_case_results=eval_case_results,
        creation_timestamp=timestamp,
    )

    # Write eval result file, with eval_set_result_name.
    app_eval_history_dir = os.path.join(
        agent_dir, app_name, ".adk", "eval_history"
    )
    if not os.path.exists(app_eval_history_dir):
      os.makedirs(app_eval_history_dir)
    # Convert to json and write to file.
    eval_set_result_json = eval_set_result.model_dump_json()
    eval_set_result_file_path = os.path.join(
        app_eval_history_dir,
        eval_set_result_name + _EVAL_SET_RESULT_FILE_EXTENSION,
    )
    logger.info("Writing eval result to file: %s", eval_set_result_file_path)
    with open(eval_set_result_file_path, "w") as f:
      f.write(json.dumps(eval_set_result_json, indent=2))

    return run_eval_results

  @app.get(
      "/apps/{app_name}/eval_results/{eval_result_id}",
      response_model_exclude_none=True,
  )
  def get_eval_result(
      app_name: str,
      eval_result_id: str,
  ) -> EvalSetResult:
    """Gets the eval result for the given eval id."""
    # Load the eval set file data
    maybe_eval_result_file_path = (
        os.path.join(
            agent_dir, app_name, ".adk", "eval_history", eval_result_id
        )
        + _EVAL_SET_RESULT_FILE_EXTENSION
    )
    if not os.path.exists(maybe_eval_result_file_path):
      raise HTTPException(
          status_code=404,
          detail=f"Eval result `{eval_result_id}` not found.",
      )
    with open(maybe_eval_result_file_path, "r") as file:
      eval_result_data = json.load(file)  # Load JSON into a list
    try:
      eval_result = EvalSetResult.model_validate_json(eval_result_data)
      return eval_result
    except ValidationError as e:
      logger.exception("get_eval_result validation error: %s", e)

  @app.get(
      "/apps/{app_name}/eval_results",
      response_model_exclude_none=True,
  )
  def list_eval_results(app_name: str) -> list[str]:
    """Lists all eval results for the given app."""
    app_eval_history_directory = os.path.join(
        agent_dir, app_name, ".adk", "eval_history"
    )
    eval_result_files = [
        file.removesuffix(_EVAL_SET_RESULT_FILE_EXTENSION)
        for file in os.listdir(app_eval_history_directory)
        if file.endswith(_EVAL_SET_RESULT_FILE_EXTENSION)
    ]
    return eval_result_files

  @app.delete("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
  def delete_session(app_name: str, user_id: str, session_id: str):
    # Connect to managed session if agent_engine_id is set.
    app_name = agent_engine_id if agent_engine_id else app_name
    session_service.delete_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}",
      response_model_exclude_none=True,
  )
  async def load_artifact(
      app_name: str,
      user_id: str,
      session_id: str,
      artifact_name: str,
      version: Optional[int] = Query(None),
  ) -> Optional[types.Part]:
    app_name = agent_engine_id if agent_engine_id else app_name
    artifact = await artifact_service.load_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
        version=version,
    )
    if not artifact:
      raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}/versions/{version_id}",
      response_model_exclude_none=True,
  )
  async def load_artifact_version(
      app_name: str,
      user_id: str,
      session_id: str,
      artifact_name: str,
      version_id: int,
  ) -> Optional[types.Part]:
    app_name = agent_engine_id if agent_engine_id else app_name
    artifact = await artifact_service.load_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
        version=version_id,
    )
    if not artifact:
      raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts",
      response_model_exclude_none=True,
  )
  async def list_artifact_names(
      app_name: str, user_id: str, session_id: str
  ) -> list[str]:
    app_name = agent_engine_id if agent_engine_id else app_name
    return await artifact_service.list_artifact_keys(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}/versions",
      response_model_exclude_none=True,
  )
  async def list_artifact_versions(
      app_name: str, user_id: str, session_id: str, artifact_name: str
  ) -> list[int]:
    app_name = agent_engine_id if agent_engine_id else app_name
    return await artifact_service.list_versions(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
    )

  @app.delete(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_name}",
  )
  async def delete_artifact(
      app_name: str, user_id: str, session_id: str, artifact_name: str
  ):
    app_name = agent_engine_id if agent_engine_id else app_name
    await artifact_service.delete_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=artifact_name,
    )

  @app.post("/run", response_model_exclude_none=True)
  async def agent_run(req: AgentRunRequest) -> list[Event]:
    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else req.app_name
    session = session_service.get_session(
        app_name=app_id, user_id=req.user_id, session_id=req.session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")
    runner = await _get_runner_async(req.app_name)
    events = [
        event
        async for event in runner.run_async(
            user_id=req.user_id,
            session_id=req.session_id,
            new_message=req.new_message,
        )
    ]
    logger.info("Generated %s events in agent run: %s", len(events), events)
    return events

  @app.post("/run_sse")
  async def agent_run_sse(req: AgentRunRequest) -> StreamingResponse:
    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else req.app_name
    # SSE endpoint
    session = session_service.get_session(
        app_name=app_id, user_id=req.user_id, session_id=req.session_id
    )
    if not session:
      raise HTTPException(status_code=404, detail="Session not found")

    # Convert the events to properly formatted SSE
    async def event_generator():
      try:
        stream_mode = StreamingMode.SSE if req.streaming else StreamingMode.NONE
        runner = await _get_runner_async(req.app_name)
        async for event in runner.run_async(
            user_id=req.user_id,
            session_id=req.session_id,
            new_message=req.new_message,
            run_config=RunConfig(streaming_mode=stream_mode),
        ):
          # Format as SSE data
          sse_event = event.model_dump_json(exclude_none=True, by_alias=True)
          logger.info("Generated event in agent run streaming: %s", sse_event)
          yield f"data: {sse_event}\n\n"
      except Exception as e:
        logger.exception("Error in event_generator: %s", e)
        # You might want to yield an error event here
        yield f'data: {{"error": "{str(e)}"}}\n\n'

    # Returns a streaming response with the proper media type for SSE
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )

  @app.get(
      "/apps/{app_name}/users/{user_id}/sessions/{session_id}/events/{event_id}/graph",
      response_model_exclude_none=True,
  )
  async def get_event_graph(
      app_name: str, user_id: str, session_id: str, event_id: str
  ):
    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else app_name
    session = session_service.get_session(
        app_name=app_id, user_id=user_id, session_id=session_id
    )
    session_events = session.events if session else []
    event = next((x for x in session_events if x.id == event_id), None)
    if not event:
      return {}

    from . import agent_graph

    function_calls = event.get_function_calls()
    function_responses = event.get_function_responses()
    root_agent = await _get_root_agent_async(app_name)
    dot_graph = None
    if function_calls:
      function_call_highlights = []
      for function_call in function_calls:
        from_name = event.author
        to_name = function_call.name
        function_call_highlights.append((from_name, to_name))
        dot_graph = await agent_graph.get_agent_graph(
            root_agent, function_call_highlights
        )
    elif function_responses:
      function_responses_highlights = []
      for function_response in function_responses:
        from_name = function_response.name
        to_name = event.author
        function_responses_highlights.append((from_name, to_name))
        dot_graph = await agent_graph.get_agent_graph(
            root_agent, function_responses_highlights
        )
    else:
      from_name = event.author
      to_name = ""
      dot_graph = await agent_graph.get_agent_graph(
          root_agent, [(from_name, to_name)]
      )
    if dot_graph and isinstance(dot_graph, graphviz.Digraph):
      return {"dot_src": dot_graph.source}
    else:
      return {}

  @app.websocket("/run_live")
  async def agent_live_run(
      websocket: WebSocket,
      app_name: str,
      user_id: str,
      session_id: str,
      modalities: List[Literal["TEXT", "AUDIO"]] = Query(
          default=["TEXT", "AUDIO"]
      ),  # Only allows "TEXT" or "AUDIO"
  ) -> None:
    await websocket.accept()

    # Connect to managed session if agent_engine_id is set.
    app_id = agent_engine_id if agent_engine_id else app_name
    session = session_service.get_session(
        app_name=app_id, user_id=user_id, session_id=session_id
    )
    if not session:
      # Accept first so that the client is aware of connection establishment,
      # then close with a specific code.
      await websocket.close(code=1002, reason="Session not found")
      return

    live_request_queue = LiveRequestQueue()

    async def forward_events():
      runner = await _get_runner_async(app_name)
      async for event in runner.run_live(
          session=session, live_request_queue=live_request_queue
      ):
        await websocket.send_text(
            event.model_dump_json(exclude_none=True, by_alias=True)
        )

    async def process_messages():
      try:
        while True:
          data = await websocket.receive_text()
          # Validate and send the received message to the live queue.
          live_request_queue.send(LiveRequest.model_validate_json(data))
      except ValidationError as ve:
        logger.error("Validation error in process_messages: %s", ve)

    # Run both tasks concurrently and cancel all if one fails.
    tasks = [
        asyncio.create_task(forward_events()),
        asyncio.create_task(process_messages()),
    ]
    done, pending = await asyncio.wait(
        tasks, return_when=asyncio.FIRST_EXCEPTION
    )
    try:
      # This will re-raise any exception from the completed tasks.
      for task in done:
        task.result()
    except WebSocketDisconnect:
      logger.info("Client disconnected during process_messages.")
    except Exception as e:
      logger.exception("Error during live websocket communication: %s", e)
      traceback.print_exc()
      WEBSOCKET_INTERNAL_ERROR_CODE = 1011
      WEBSOCKET_MAX_BYTES_FOR_REASON = 123
      await websocket.close(
          code=WEBSOCKET_INTERNAL_ERROR_CODE,
          reason=str(e)[:WEBSOCKET_MAX_BYTES_FOR_REASON],
      )
    finally:
      for task in pending:
        task.cancel()

  def _get_all_toolsets(agent: BaseAgent) -> set[BaseToolset]:
    toolsets = set()
    if isinstance(agent, LlmAgent):
      for tool_union in agent.tools:
        if isinstance(tool_union, BaseToolset):
          toolsets.add(tool_union)
    for sub_agent in agent.sub_agents:
      toolsets.update(_get_all_toolsets(sub_agent))
    return toolsets

  async def _get_root_agent_async(app_name: str) -> Agent:
    """Returns the root agent for the given app."""
    if app_name in root_agent_dict:
      return root_agent_dict[app_name]
    agent_module = importlib.import_module(app_name)
    if getattr(agent_module.agent, "root_agent"):
      root_agent = agent_module.agent.root_agent
    else:
      raise ValueError(f'Unable to find "root_agent" from {app_name}.')

    # Handle an awaitable root agent and await for the actual agent.
    if inspect.isawaitable(root_agent):
      try:
        agent, exit_stack = await root_agent
        exit_stacks.append(exit_stack)
        root_agent = agent
      except Exception as e:
        raise RuntimeError(f"error getting root agent, {e}") from e

    root_agent_dict[app_name] = root_agent
    toolsets_to_close.update(_get_all_toolsets(root_agent))
    return root_agent

  async def _get_runner_async(app_name: str) -> Runner:
    """Returns the runner for the given app."""
    envs.load_dotenv_for_agent(os.path.basename(app_name), agent_dir)
    if app_name in runner_dict:
      return runner_dict[app_name]
    root_agent = await _get_root_agent_async(app_name)
    runner = Runner(
        app_name=agent_engine_id if agent_engine_id else app_name,
        agent=root_agent,
        artifact_service=artifact_service,
        session_service=session_service,
        memory_service=memory_service,
    )
    runner_dict[app_name] = runner
    return runner

  if web:
    BASE_DIR = Path(__file__).parent.resolve()
    ANGULAR_DIST_PATH = BASE_DIR / "browser"

    @app.get("/")
    async def redirect_to_dev_ui():
      return RedirectResponse("/dev-ui")

    @app.get("/dev-ui")
    async def dev_ui():
      return FileResponse(BASE_DIR / "browser/index.html")

    app.mount(
        "/", StaticFiles(directory=ANGULAR_DIST_PATH, html=True), name="static"
    )
  return app
