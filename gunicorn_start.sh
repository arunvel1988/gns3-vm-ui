#!/bin/bash
source venv/bin/activate
exec gunicorn -w 3 -b 0.0.0.0:5000 app:app
