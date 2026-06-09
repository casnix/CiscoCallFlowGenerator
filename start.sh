#!/usr/bin/env bash

source ./venv/bin/activate
cd ./src
uvicorn main:app --reload --port 8000
deactivate