# some_sim

A small FastAPI app. Python serves a JSON API under `/api` and plain HTML pages from `static/`
(the page uses React from a CDN, so there's no build step).

```sh
pip install -r requirements.txt
uvicorn main:app --reload --port 8000   # http://localhost:8000
```
