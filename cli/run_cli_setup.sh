#!/bin/bash
# Virtus MCP CLI Setup Script
# Launches MCP DB Server, MCP FS Server, and CLI in separate Terminal windows

set -e

# Get the project root directory (parent of cli/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Virtus MCP CLI Setup${NC}"
echo -e "${BLUE}====================${NC}"
echo ""

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "${YELLOW}Warning: This script is optimized for macOS.${NC}"
    echo "On other systems, you may need to run the servers manually."
    echo ""
fi

# Function to open a new Terminal window and run a command
open_terminal() {
    local title=$1
    local command=$2

    if [[ "$OSTYPE" == "darwin"* ]]; then
        osascript <<EOF
tell application "Terminal"
    activate
    set newTab to do script "cd '$PROJECT_ROOT' && $command"
    set custom title of newTab to "$title"
end tell
EOF
    else
        echo -e "${YELLOW}Please run in a new terminal:${NC}"
        echo "cd $PROJECT_ROOT"
        echo "$command"
        echo ""
    fi
}

# Terminal 1: MCP DB Server
echo -e "${GREEN}Starting MCP DB Server (Terminal 1)...${NC}"
open_terminal "MCP DB Server" "python mcp/db_server/main.py"
sleep 2

# Terminal 2: MCP FS Server
echo -e "${GREEN}Starting MCP FS Server (Terminal 2)...${NC}"
open_terminal "MCP FS Server" "python mcp/fs_server/main.py"
sleep 2

# Terminal 3: CLI Client (with environment variables)
echo -e "${GREEN}Starting CLI Client (Terminal 3)...${NC}"
CLI_COMMAND='export MCP_DB_SERVER_URL=http://localhost:8080 && export MCP_FS_SERVER_URL=http://localhost:8081 && echo "MCP environment variables set" && echo "MCP_DB_SERVER_URL=$MCP_DB_SERVER_URL" && echo "MCP_FS_SERVER_URL=$MCP_FS_SERVER_URL" && echo "" && echo "Waiting 3 seconds for servers to start..." && sleep 3 && python cli/cli.py check-mcp && echo "" && echo "Starting interactive CLI..." && python cli/cli.py client'

open_terminal "Virtus CLI" "$CLI_COMMAND"

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "Three Terminal windows should now be open:"
echo "  1. MCP DB Server (port 8080)"
echo "  2. MCP FS Server (port 8081)"
echo "  3. Virtus CLI (interactive mode)"
echo ""
echo -e "${YELLOW}Note:${NC} If you need to set OPENAI_API_KEY, add it to Terminal 3:"
echo "  export OPENAI_API_KEY=your_key_here"
echo ""
