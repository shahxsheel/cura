cd ~/cura/piper_sdk

# Record — drag the arm in teach mode, hit Enter to capture each waypoint
python3 record_waypoints.py --output bottle_pick2.json --name pickup

# Replay — run a saved sequence
python3 replay_waypoints.py --file bottle_pick1.json --speed 50
