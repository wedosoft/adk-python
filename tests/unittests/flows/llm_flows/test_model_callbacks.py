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

from typing import Any
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.models import LlmResponse
from google.genai import types
from pydantic import BaseModel
import pytest

from ... import utils


class MockBeforeModelCallback(BaseModel):
  mock_response: str

  def __call__(
      self,
      callback_context: CallbackContext,
      llm_request: LlmRequest,
  ) -> LlmResponse:
    return LlmResponse(
        content=utils.ModelContent(
            [types.Part.from_text(text=self.mock_response)]
        )
    )


class MockAfterModelCallback(BaseModel):
  mock_response: str

  def __call__(
      self,
      callback_context: CallbackContext,
      llm_response: LlmResponse,
  ) -> LlmResponse:
    return LlmResponse(
        content=utils.ModelContent(
            [types.Part.from_text(text=self.mock_response)]
        )
    )


def noop_callback(**kwargs) -> Optional[LlmResponse]:
  pass


def test_before_model_callback():
  responses = ['model_response']
  mock_model = utils.MockModel.create(responses=responses)
  agent = Agent(
      name='root_agent',
      model=mock_model,
      before_model_callback=MockBeforeModelCallback(
          mock_response='before_model_callback'
      ),
  )

  runner = utils.InMemoryRunner(agent)
  assert utils.simplify_events(runner.run('test')) == [
      ('root_agent', 'before_model_callback'),
  ]


def test_before_model_callback_noop():
  responses = ['model_response']
  mock_model = utils.MockModel.create(responses=responses)
  agent = Agent(
      name='root_agent',
      model=mock_model,
      before_model_callback=noop_callback,
  )

  runner = utils.InMemoryRunner(agent)
  assert utils.simplify_events(runner.run('test')) == [
      ('root_agent', 'model_response'),
  ]


def test_before_model_callback_end():
  responses = ['model_response']
  mock_model = utils.MockModel.create(responses=responses)
  agent = Agent(
      name='root_agent',
      model=mock_model,
      before_model_callback=MockBeforeModelCallback(
          mock_response='before_model_callback',
      ),
  )

  runner = utils.InMemoryRunner(agent)
  assert utils.simplify_events(runner.run('test')) == [
      ('root_agent', 'before_model_callback'),
  ]


def test_after_model_callback():
  responses = ['model_response']
  mock_model = utils.MockModel.create(responses=responses)
  agent = Agent(
      name='root_agent',
      model=mock_model,
      after_model_callback=MockAfterModelCallback(
          mock_response='after_model_callback'
      ),
  )

  runner = utils.InMemoryRunner(agent)
  assert utils.simplify_events(runner.run('test')) == [
      ('root_agent', 'after_model_callback'),
  ]


@pytest.mark.asyncio
async def test_after_model_callback_noop():
  responses = ['model_response']
  mock_model = utils.MockModel.create(responses=responses)
  agent = Agent(
      name='root_agent',
      model=mock_model,
      after_model_callback=noop_callback,
  )

  runner = utils.TestInMemoryRunner(agent)
  assert utils.simplify_events(
      await runner.run_async_with_new_session('test')
  ) == [('root_agent', 'model_response')]
