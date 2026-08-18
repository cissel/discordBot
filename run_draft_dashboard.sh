#!/bin/bash
cd /home/jhcv/discordBot
exec ./venv/bin/python3 python/nflDraftDashboard.py --port 8877 --host 0.0.0.0
