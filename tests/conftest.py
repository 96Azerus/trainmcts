import sys
import os

# Add the project root directory to sys.path
# This allows pytest to find modules like ofc_logic and mcts_node
# when tests are run from the tests/ directory.
# The project root is one level up from the tests directory.
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)
