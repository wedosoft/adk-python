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

import enum
import pytest

from google.adk.events import Event
from google.adk.events import EventActions
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types


class SessionServiceType(enum.Enum):
  IN_MEMORY = 'IN_MEMORY'
  DATABASE = 'DATABASE'


def get_session_service(
    service_type: SessionServiceType = SessionServiceType.IN_MEMORY,
):
  """Creates a session service for testing."""
  if service_type == SessionServiceType.DATABASE:
    return DatabaseSessionService('sqlite:///:memory:')
  return InMemorySessionService()


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_get_empty_session(service_type):
  session_service = get_session_service(service_type)
  assert not session_service.get_session(
      app_name='my_app', user_id='test_user', session_id='123'
  )


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_create_get_session(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'test_user'
  state = {'key': 'value'}

  session = session_service.create_session(
      app_name=app_name, user_id=user_id, state=state
  )
  assert session.app_name == app_name
  assert session.user_id == user_id
  assert session.id
  assert session.state == state
  assert (
      session_service.get_session(
          app_name=app_name, user_id=user_id, session_id=session.id
      )
      == session
  )

  session_id = session.id
  session_service.delete_session(
      app_name=app_name, user_id=user_id, session_id=session_id
  )

  assert (
      not session_service.get_session(
          app_name=app_name, user_id=user_id, session_id=session.id
      )
      == session
  )


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_create_and_list_sessions(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'test_user'

  session_ids = ['session' + str(i) for i in range(5)]
  for session_id in session_ids:
    session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

  sessions = session_service.list_sessions(
      app_name=app_name, user_id=user_id
  ).sessions
  for i in range(len(sessions)):
    assert sessions[i].id == session_ids[i]


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_session_state(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id_1 = 'user1'
  user_id_2 = 'user2'
  session_id_11 = 'session11'
  session_id_12 = 'session12'
  session_id_2 = 'session2'
  state_11 = {'key11': 'value11'}
  state_12 = {'key12': 'value12'}

  session_11 = session_service.create_session(
      app_name=app_name,
      user_id=user_id_1,
      state=state_11,
      session_id=session_id_11,
  )
  session_service.create_session(
      app_name=app_name,
      user_id=user_id_1,
      state=state_12,
      session_id=session_id_12,
  )
  session_service.create_session(
      app_name=app_name, user_id=user_id_2, session_id=session_id_2
  )

  assert session_11.state.get('key11') == 'value11'

  event = Event(
      invocation_id='invocation',
      author='user',
      content=types.Content(role='user', parts=[types.Part(text='text')]),
      actions=EventActions(
          state_delta={
              'app:key': 'value',
              'user:key1': 'value1',
              'temp:key': 'temp',
              'key11': 'value11_new',
          }
      ),
  )
  session_service.append_event(session=session_11, event=event)

  # User and app state is stored, temp state is filtered.
  assert session_11.state.get('app:key') == 'value'
  assert session_11.state.get('key11') == 'value11_new'
  assert session_11.state.get('user:key1') == 'value1'
  assert not session_11.state.get('temp:key')

  session_12 = session_service.get_session(
      app_name=app_name, user_id=user_id_1, session_id=session_id_12
  )
  # After getting a new instance, the session_12 got the user and app state,
  # even append_event is not applied to it, temp state has no effect
  assert session_12.state.get('key12') == 'value12'
  assert not session_12.state.get('temp:key')

  # The user1's state is not visible to user2, app state is visible
  session_2 = session_service.get_session(
      app_name=app_name, user_id=user_id_2, session_id=session_id_2
  )
  assert session_2.state.get('app:key') == 'value'
  assert not session_2.state.get('user:key1')

  assert not session_2.state.get('user:key1')

  # The change to session_11 is persisted
  session_11 = session_service.get_session(
      app_name=app_name, user_id=user_id_1, session_id=session_id_11
  )
  assert session_11.state.get('key11') == 'value11_new'
  assert session_11.state.get('user:key1') == 'value1'
  assert not session_11.state.get('temp:key')


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_create_new_session_will_merge_states(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'user'
  session_id_1 = 'session1'
  session_id_2 = 'session2'
  state_1 = {'key1': 'value1'}

  session_1 = session_service.create_session(
      app_name=app_name, user_id=user_id, state=state_1, session_id=session_id_1
  )

  event = Event(
      invocation_id='invocation',
      author='user',
      content=types.Content(role='user', parts=[types.Part(text='text')]),
      actions=EventActions(
          state_delta={
              'app:key': 'value',
              'user:key1': 'value1',
              'temp:key': 'temp',
          }
      ),
  )
  session_service.append_event(session=session_1, event=event)

  # User and app state is stored, temp state is filtered.
  assert session_1.state.get('app:key') == 'value'
  assert session_1.state.get('key1') == 'value1'
  assert session_1.state.get('user:key1') == 'value1'
  assert not session_1.state.get('temp:key')

  session_2 = session_service.create_session(
      app_name=app_name, user_id=user_id, state={}, session_id=session_id_2
  )
  # Session 2 has the persisted states
  assert session_2.state.get('app:key') == 'value'
  assert session_2.state.get('user:key1') == 'value1'
  assert not session_2.state.get('key1')
  assert not session_2.state.get('temp:key')


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_append_event_bytes(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'user'

  session = session_service.create_session(app_name=app_name, user_id=user_id)
  event = Event(
      invocation_id='invocation',
      author='user',
      content=types.Content(
          role='user',
          parts=[
              types.Part.from_bytes(
                  data=b'test_image_data', mime_type='image/png'
              ),
          ],
      ),
  )
  session_service.append_event(session=session, event=event)

  assert session.events[0].content.parts[0] == types.Part.from_bytes(
      data=b'test_image_data', mime_type='image/png'
  )

  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id
  ).events
  assert len(events) == 1
  assert events[0].content.parts[0] == types.Part.from_bytes(
      data=b'test_image_data', mime_type='image/png'
  )


@pytest.mark.parametrize(
    'service_type', [SessionServiceType.IN_MEMORY, SessionServiceType.DATABASE]
)
def test_append_event_complete(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'user'

  session = session_service.create_session(app_name=app_name, user_id=user_id)
  event = Event(
      invocation_id='invocation',
      author='user',
      content=types.Content(role='user', parts=[types.Part(text='test_text')]),
      turn_complete=True,
      partial=False,
      actions=EventActions(
          artifact_delta={
              'file': 0,
          },
          transfer_to_agent='agent',
          escalate=True,
      ),
      long_running_tool_ids={'tool1'},
      error_code='error_code',
      error_message='error_message',
      interrupted=True,
  )
  session_service.append_event(session=session, event=event)

  assert (
      session_service.get_session(
          app_name=app_name, user_id=user_id, session_id=session.id
      )
      == session
  )

@pytest.mark.parametrize('service_type', [SessionServiceType.IN_MEMORY])
def test_get_session_with_config(service_type):
  session_service = get_session_service(service_type)
  app_name = 'my_app'
  user_id = 'user'

  num_test_events = 5
  session = session_service.create_session(app_name=app_name, user_id=user_id)
  for i in range(1, num_test_events + 1):
    event = Event(author='user', timestamp=i)
    session_service.append_event(session, event)

  # No config, expect all events to be returned.
  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id
  ).events
  assert len(events) == num_test_events

  # Only expect the most recent 3 events.
  num_recent_events = 3
  config = GetSessionConfig(num_recent_events=num_recent_events)
  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id, config=config
  ).events
  assert len(events) == num_recent_events
  assert events[0].timestamp == num_test_events - num_recent_events + 1

  # Only expect events after timestamp 4.0 (inclusive), i.e., 2 events.
  after_timestamp = 4.0
  config = GetSessionConfig(after_timestamp=after_timestamp)
  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id, config=config
  ).events
  assert len(events) == num_test_events - after_timestamp + 1
  assert events[0].timestamp == after_timestamp

  # Expect no events if none are > after_timestamp.
  way_after_timestamp = num_test_events * 10
  config = GetSessionConfig(after_timestamp=way_after_timestamp)
  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id, config=config
  ).events
  assert len(events) == 0

  # Both filters applied, i.e., of 3 most recent events, only 2 are after
  # timestamp 4.0, so expect 2 events.
  config = GetSessionConfig(
      after_timestamp=after_timestamp, num_recent_events=num_recent_events
  )
  events = session_service.get_session(
      app_name=app_name, user_id=user_id, session_id=session.id, config=config
  ).events
  assert len(events) == num_test_events - after_timestamp + 1
