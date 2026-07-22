"""Minimal example: start the Remedy API server with a BasicRuntime agent."""

import asyncio

import uvicorn

from remedy.core.agent import BasicRuntime
from remedy.gateway.router import Gateway
from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore
from remedy.models import AgentConfig


async def main():
    memory = MemoryStore("remedy_example.db")
    await memory.initialize()

    config = AgentConfig(
        name="ExampleAgent",
        home_dir=".",
        llm_api_key="",  # set REMEDY_LLM_API_KEY env var for LLM responses
    )

    runtime = BasicRuntime(config, memory=memory)
    await runtime.start()

    # Discover skills from ./skills
    import os
    skills_dir = os.path.join(".", "skills")
    if os.path.isdir(skills_dir):
        runtime.skills.discover(skills_dir, recurse=True)

    gateway = Gateway(runtime=runtime, memory_store=memory)
    gateway.register_handler(runtime.handle_event)
    await gateway.start()

    app = create_app(
        runtime=runtime,
        gateway=gateway,
        memory=memory,
        api_key="example-key",
    )

    print("Server running at http://127.0.0.1:8399")
    print("API docs at http://127.0.0.1:8399/docs")
    print("Auth header: Authorization: Bearer example-key")
    uvicorn.run(app, host="127.0.0.1", port=8399)


if __name__ == "__main__":
    asyncio.run(main())
