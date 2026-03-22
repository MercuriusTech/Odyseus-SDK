#!/bin/bash

# Navigate to the project directory
cd ~/odyseus_pi || exit

# Activate the virtual environment and run the python script in the background
# We use 'source' inside the subshell
source venv/bin/activate

echo "Starting pi_client.py..."

# Run the script. 
# If you have another command to run "at the same time", 
# place it here followed by an '&'
python pi_client.py --p 8200

# Note: If you meant you want to run 'source' and 'python' together, 
# they must be in the same process, which the script above handles.