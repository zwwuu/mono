"""Frozen/CLI entry point for workflow-app: run the uvicorn server."""

import uvicorn

from apps.workflow_app.main import app


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
