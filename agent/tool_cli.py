"""JSON CLI for local Agents that use shell/function tools instead of MCP."""
import argparse
import json
from mcp_server import Server, TOOLS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=[tool['name'] for tool in TOOLS])
    parser.add_argument('--arguments', default='{}')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535: parser.error('Invalid local port')
    if len(args.arguments.encode()) > 8192: parser.error('Arguments exceed limit')
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict): raise ValueError('Arguments must be an object')
        result = Server(args.port).invoke(args.name, arguments)
    except ValueError:
        parser.error('Invalid tool arguments')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
