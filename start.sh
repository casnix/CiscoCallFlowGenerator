#!/bin/sh

cd ./src
uvicorn main:app --reload --port 8000