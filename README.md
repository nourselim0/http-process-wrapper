# HTTP Process Wrapper

A simple microservice to wrap command line processes and expose them over HTTP using FastAPI, maybe one day it can be like supervisord but as a web service.

## Installation
Make sure you have [Poetry](https://python-poetry.org/docs/#installation) installed, then run:

```bash
poetry install
```

## Running the Server
To start the server, use the following command:

```bash
poetry run uvicorn app.main:app
```

> Note: when running on Windows you can't use reload because uvicorn will then use an asyncio loop implementation that does not support subprocesses.

## API Docs
Once the server is running, you can access the interactive API documentation at `http://127.0.0.1:8000/docs`

## Features
- Start/Stop/Restart command line processes via HTTP
- List all managed processes with their exit codes and pids
- Polling stdout and stderr of running processes
- Send input to stdin of running processes

## Future Plans
- Dockerization (need to see if this can run as a sidecar container 👀)
- Websocket support for real-time process output
- Optional API Key authentication
- Run default processes on launch
- Some kind of persistence
