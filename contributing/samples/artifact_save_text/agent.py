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


from google.adk import Agent
from google.adk.tools.tool_context import ToolContext
from google.genai import types


async def log_query(tool_context: ToolContext, query: str):
  """Roll a die with the specified number of sides."""
  await tool_context.save_artifact('query', types.Part(text=query))


root_agent = Agent(
    model='gemini-2.0-flash-exp',
    name='log_agent',
    description='Log user query.',
    instruction="""Always log the user query and reploy "kk, I've logged."
    """,
    tools=[log_query],
    generate_content_config=types.GenerateContentConfig(
        safety_settings=[
            types.SafetySetting(  # avoid false alarm about rolling dice.
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
        ]
    ),
)
